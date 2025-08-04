"""Helper functions for evaluation - adapted from WebArena to use our config system"""

import json
import logging
from urllib.parse import urlparse

import requests


class HelperFunctions:
    """Helper functions for evaluation that use our config system"""

    def __init__(self, config, extra_headers):
        """Initialize with our config containing accounts and site URLs"""
        self.config = config
        self.logger = logging.getLogger(__name__)
        self.extra_headers = extra_headers

        # Set up proxies if proxy is enabled
        self.proxies = None
        if config.proxy.enabled:
            self.proxies = {
                "http": config.proxy.server,
                "https": config.proxy.server,
            }

    def _get_site_url(self, site_name: str) -> str:
        """Get site URL from config"""
        site_host = self.config.sites[site_name]
        return f"http://{site_host}"

    def _get_account_info(self, account_key: str) -> dict:
        """Get account info from config"""
        return self.config.accounts[account_key]

    def shopping_get_auth_token(self) -> str:
        """Get shopping site auth token"""
        shopping_url = self._get_site_url("shopping")
        admin_account = self._get_account_info("shopping_admin")

        headers = {"content-type": "application/json"}
        headers.update(self.extra_headers)

        self.logger.info(f"Shopping auth request - URL: {shopping_url}/rest/default/V1/integration/admin/token")
        self.logger.info(f"Shopping auth request - Headers: {headers}")
        self.logger.info(f"Shopping auth request - Proxies: {self.proxies}")

        response = requests.post(
            url=f"{shopping_url}/rest/default/V1/integration/admin/token",
            headers=headers,
            data=json.dumps(
                {
                    "username": admin_account["username"],
                    "password": admin_account["password"],
                }
            ),
            proxies=self.proxies,
            timeout=30,
        )
        self.logger.info(f"Shopping auth response status: {response.status_code}")
        response.raise_for_status()
        token: str = response.json()
        return token

    def shopping_get_latest_order_url(self) -> str:
        """Get the latest order url from the shopping website."""
        shopping_url = self._get_site_url("shopping")
        token = self.shopping_get_auth_token()

        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        }
        headers.update(self.extra_headers)

        params = {
            "searchCriteria[sortOrders][0][field]": "created_at",
            "searchCriteria[sortOrders][0][direction]": "DESC",
            "searchCriteria[pageSize]": "1",
            "searchCriteria[filter_groups][0][filters][0][field]": "customer_id",
            "searchCriteria[filter_groups][0][filters][0][value]": "27",
        }

        response = requests.get(f"{shopping_url}/rest/V1/orders", params=params, headers=headers, proxies=self.proxies, timeout=30)
        response.raise_for_status()

        response_obj = response.json()
        order_item = response_obj["items"][0]
        order_id = int(order_item["increment_id"])
        order_url = f"{shopping_url}/sales/order/view/order_id/{order_id}/"
        return order_url

    def shopping_get_sku_latest_review_author(self, sku: str) -> str:
        """Get the latest review author for a product SKU."""
        shopping_url = self._get_site_url("shopping")
        token = self.shopping_get_auth_token()

        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        }
        headers.update(self.extra_headers)

        response = requests.get(f"{shopping_url}/rest/V1/products/{sku}/reviews", headers=headers, proxies=self.proxies, timeout=30)
        response.raise_for_status()

        response_obj = response.json()
        author: str = response_obj[-1]["nickname"]
        return author

    def shopping_get_sku_latest_review_rating(self, sku: str) -> str:
        """Get the latest review rating for a product SKU."""
        shopping_url = self._get_site_url("shopping")
        token = self.shopping_get_auth_token()

        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        }
        headers.update(self.extra_headers)

        response = requests.get(f"{shopping_url}/rest/V1/products/{sku}/reviews", headers=headers, proxies=self.proxies, timeout=30)
        response.raise_for_status()

        response_obj = response.json()
        latest_review = response_obj[-1]
        rating: str = str(latest_review["ratings"][0]["percent"])
        return rating

    def reddit_get_post_url(self, url: str) -> str:
        """Get the post url from a Reddit comment/post URL"""
        # Url is http://domain/f/subreddit/post_id/...
        # get domain, subreddit, post_id
        parsed = urlparse(url)
        domain = parsed.netloc
        tok_url = parsed.path.split("/")

        # Validate URL structure - fail fast if invalid
        assert len(tok_url) >= 4, f"Invalid Reddit URL structure: {url}"
        assert tok_url[1] == "f", f"Not a Reddit forum URL: {url}"

        subreddit = tok_url[2]
        post_id = tok_url[3]
        scheme = parsed.scheme
        post_url = f"{scheme}://{domain}/f/{subreddit}/{post_id}/"
        return post_url

    async def gitlab_get_project_member_role(self, page, account_name: str) -> str:
        """Get project member role from GitLab page (async version)"""
        # get the account index
        account_idx = await page.evaluate(
            f"""(() => {{
                const elements = document.querySelectorAll("td[data-label='Account'] span.gl-avatar-labeled-sublabel");
                let index = -1;  // Default value if not found

                for(let i = 0; i < elements.length; i++) {{
                    if(elements[i].outerText === '@{account_name}') {{
                        index = i;
                        break;
                    }}
                }}

                return index;
            }})()"""
        )

        # Fail fast if account not found
        assert account_idx != -1, f"Account {account_name} not found on GitLab page"

        # get the role
        role: str = await page.evaluate(
            f"""(() => {{
                const roleElements = document.querySelectorAll("td.col-max-role span");
                return roleElements[{account_idx}].outerText;
            }})()"""
        )
        return role

    async def llm_fuzzy_match(self, pred: str, reference: str, question: str) -> float:
        """Use our in-house LLM for fuzzy matching evaluation"""
        from rl_web_agent.llm import get_llm_client

        # Get singleton LLM client
        llm_client = get_llm_client()

        # Construct evaluation prompt
        # Load prompt from file
        from rl_web_agent.prompts import load_prompt

        user_prompt = load_prompt("fuzzy_match_evaluator").format(question=question, reference=reference, pred=pred)

        messages = [{"role": "user", "content": user_prompt}]

        response = await llm_client.complete(messages)
        response_lower = response.lower().strip()

        if "partially_correct" in response_lower or "incorrect" in response_lower:
            return 0.0
        elif "correct" in response_lower:
            return 1.0
        else:
            # Fail fast - don't provide fallbacks for unclear responses
            raise ValueError(f"Unclear LLM response for fuzzy match: {response}")

    async def llm_ua_match(self, pred: str, reference: str, question: str) -> float:
        """Use our in-house LLM for unachievable task matching"""
        from rl_web_agent.llm import get_llm_client

        # Get singleton LLM client
        llm_client = get_llm_client()

        # Load prompt from file
        from rl_web_agent.prompts import load_prompt

        user_prompt = load_prompt("ua_match_evaluator").format(question=question, reference=reference, pred=pred)

        messages = [{"role": "user", "content": user_prompt}]

        response = await llm_client.complete(messages)
        response_lower = response.lower().strip()

        if "different" in response_lower:
            return 0.0
        elif "same" in response_lower:
            return 1.0
        else:
            # Fail fast - don't provide fallbacks for unclear responses
            raise ValueError(f"Unclear LLM response for UA match: {response}")


# Global helper instance - will be initialized when needed
_helper_instance = None


def get_helper_functions(config, extra_headers) -> HelperFunctions:
    """Get or create helper functions instance with config"""
    global _helper_instance
    if _helper_instance is None:
        _helper_instance = HelperFunctions(config, extra_headers)
    return _helper_instance


def shopping_get_latest_order_url(config=None, extra_headers=None) -> str:
    """Global function for backward compatibility"""
    helper = get_helper_functions(config, extra_headers or {})
    return helper.shopping_get_latest_order_url()


def shopping_get_sku_latest_review_author(sku: str, config=None, extra_headers=None) -> str:
    """Global function for backward compatibility"""
    helper = get_helper_functions(config, extra_headers or {})
    return helper.shopping_get_sku_latest_review_author(sku)


def shopping_get_sku_latest_review_rating(sku: str, config=None, extra_headers=None) -> str:
    """Global function for backward compatibility"""
    helper = get_helper_functions(config, extra_headers or {})
    return helper.shopping_get_sku_latest_review_rating(sku)


def reddit_get_post_url(url: str, config=None, extra_headers=None) -> str:
    """Global function for backward compatibility"""
    helper = get_helper_functions(config, extra_headers or {})
    return helper.reddit_get_post_url(url)


async def gitlab_get_project_member_role(page, account_name: str, config=None, extra_headers=None) -> str:
    """Global function for backward compatibility"""
    helper = get_helper_functions(config, extra_headers or {})
    return await helper.gitlab_get_project_member_role(page, account_name)
