"""WebArena evaluator - ported to async and simplified for RL Web Agent"""

import collections
import html
import urllib
from typing import Any

from beartype import beartype
from nltk.tokenize import word_tokenize


class StringEvaluator:
    """Check whether the answer is correct with:
    exact match
    must include
    fuzzy match, using LLM judge (simplified for now)
    """

    @staticmethod
    @beartype
    def clean_answer(answer):
        answer = answer.strip()
        if answer.startswith("'") and answer.endswith("'"):
            answer = answer[1:-1]
        elif answer.startswith('"') and answer.endswith('"'):
            answer = answer[1:-1]
        return answer.lower()

    @staticmethod
    @beartype
    def exact_match(ref, pred):
        return float(StringEvaluator.clean_answer(pred) == StringEvaluator.clean_answer(ref))

    @staticmethod
    @beartype
    def must_include(ref, pred, tokenize):
        clean_ref = StringEvaluator.clean_answer(ref)
        clean_pred = StringEvaluator.clean_answer(pred)
        # tokenize the answer if the ref is a single word
        # prevent false positive (e.g, 0)
        if tokenize and len(clean_ref) == 1 and len(word_tokenize(clean_ref)) == 1:
            tok_pred = word_tokenize(clean_pred)
            return float(clean_ref in tok_pred)
        else:
            return float(clean_ref in clean_pred)

    @staticmethod
    @beartype
    def fuzzy_match(ref, pred, intent):
        # Simplified fuzzy match - just return exact match for now
        return 1.0 if pred.lower().strip() == ref.lower().strip() else 0.0

    @staticmethod
    @beartype
    def ua_match(ref, pred, intent):
        # Simplified UA match - just return exact match for now
        return 1.0 if pred.lower().strip() == ref.lower().strip() else 0.0

    async def evaluate(self, answer: str, config: dict[str, Any]) -> float:
        """Evaluate answer against reference answers in config"""
        pred = self.clean_answer(answer)

        score = 1.0
        for approach, value in config["eval"]["reference_answers"].items():
            match approach:
                case "exact_match":
                    score *= self.exact_match(ref=value, pred=pred)

                case "must_include":
                    assert isinstance(value, list)
                    for must_value in value:
                        score *= self.must_include(
                            ref=must_value,
                            pred=pred,
                            tokenize=(len(value) == 1),
                        )
                case "fuzzy_match":
                    intent = config["intent"]
                    if value == "N/A":
                        # if the instruction only asks the model to generate N/A when encountering an unachievable task
                        # without more concrete reasons
                        score *= self.exact_match(ref=value, pred=pred)
                        # if the instruction also asks the model to generate the reason why the task is unachievable
                        # this should be the default as it will prevent false positive N/A`
                        if score != 1:
                            score = 1.0 * self.ua_match(
                                intent=config["intent"],
                                ref=config["eval"]["string_note"],
                                pred=pred,
                            )
                    else:
                        assert isinstance(value, list)
                        for reference in value:
                            score *= self.fuzzy_match(ref=reference, pred=pred, intent=intent)
        return score


class URLEvaluator:
    """Check URL matching"""

    async def evaluate(self, page_url: str, config: dict[str, Any]) -> float:
        """Evaluate current page URL against reference URL"""

        def clean_url(url):
            url = str(url)
            url = url.rstrip("/")
            return url

        def parse_url(url):
            """Parse a URL into its base, path, and query components."""
            parsed_url = urllib.parse.urlparse(url)
            base_path = parsed_url.netloc + parsed_url.path
            query = urllib.parse.parse_qs(parsed_url.query)
            return base_path, query

        def parse_urls(urls):
            """Parse a list of URLs."""
            base_paths = []
            queries = collections.defaultdict(set)
            for url in urls:
                base_path, query = parse_url(url)
                base_paths.append(base_path)
                for k, v in query.items():
                    queries[k].update(v)
            return base_paths, queries

        pred = clean_url(page_url)
        ref_urls = config["eval"]["reference_url"].split(" |OR| ")
        ref_urls = [clean_url(url) for url in ref_urls]
        matching_rule = config["eval"].get("url_note", "GOLD in PRED")
        if matching_rule == "GOLD in PRED":
            ref_base_paths, ref_queries = parse_urls(ref_urls)
            pred_base_paths, pred_query = parse_url(pred)

            base_score = float(any([ref_base_path in pred_base_paths for ref_base_path in ref_base_paths]))
            query_score = 1.0
            for k, possible_values in ref_queries.items():
                query_score *= float(any(possible_ref_value in pred_query.get(k, []) for possible_ref_value in possible_values))
            score = base_score * query_score

        else:
            raise ValueError(f"Unknown matching rule: {matching_rule}")

        return score


class HTMLContentEvaluator:
    """Check whether the contents appear in the page"""

    async def evaluate(self, page, config: dict[str, Any]) -> float:
        """Evaluate page content against required content"""
        targets = config["eval"]["program_html"]

        score = 1.0
        for target in targets:
            target_url = target["url"]  # which url to check
            if target_url.startswith("func"):
                func = target_url.split("func:")[1]
                func = func.replace("__last_url__", page.url)
                # Import helper functions when needed
                if "shopping_get_latest_order_url" in func:
                    pass
                target_url = eval(func)

            locator = target["locator"]  # js element locator

            # navigate to that url
            if target_url != "last":
                await page.goto(target_url)
                await page.wait_for_load_state("networkidle")

            # empty, use the full page
            if not locator.strip():
                selected_element = await page.content()
            # use JS to select the element
            elif locator.startswith("document.") or locator.startswith("[...document."):
                if "prep_actions" in target:
                    try:
                        for prep_action in target["prep_actions"]:
                            await page.evaluate(f"() => {prep_action}")
                    except Exception:
                        pass
                try:
                    selected_element = str(await page.evaluate(f"() => {locator}"))
                    if not selected_element:
                        selected_element = ""
                except Exception:
                    # the page is wrong, return empty
                    selected_element = ""
            # run program to call API
            elif locator.startswith("func:"):  # a helper function
                func = locator.split("func:")[1]
                func = func.replace("__page__", "page")
                # Import helper functions when needed
                if "shopping_get_" in func or "reddit_get_" in func or "gitlab_get_" in func:
                    pass
                selected_element = eval(func)
            else:
                raise ValueError(f"Unknown locator: {locator}")

            selected_element = html.unescape(selected_element)

            if "exact_match" in target["required_contents"]:
                required_contents = target["required_contents"]["exact_match"]
                cur_score = StringEvaluator.exact_match(ref=required_contents, pred=selected_element)
                score *= float(cur_score)
            elif "must_include" in target["required_contents"]:
                required_contents = target["required_contents"]["must_include"]
                assert isinstance(required_contents, list)
                for content in required_contents:
                    content_or = content.split(" |OR| ")
                    cur_score = any(
                        [
                            StringEvaluator.must_include(
                                ref=content,
                                pred=selected_element,
                                tokenize=False,
                            )
                            for content in content_or
                        ]
                    )
                    score *= float(cur_score)
            else:
                raise ValueError(f"Unknown required_contents: {target['required_contents'].keys()}")
        return score


async def evaluate_task(answer: str, page, config: dict[str, Any]) -> float:
    """
    Evaluate a task using the provided answer, page, and configuration.
    Uses WebArena's exact evaluation logic.

    Args:
        answer: The model's final answer
        page: Playwright page object
        config: Task configuration dict with eval section

    Returns:
        float: Score between 0.0 and 1.0
    """
    eval_types = config["eval"]["eval_types"]
    total_score = 1.0

    for eval_type in eval_types:
        if eval_type == "string_match":
            evaluator = StringEvaluator()
            score = await evaluator.evaluate(answer, config)
            total_score *= score
        elif eval_type == "url_match":
            evaluator = URLEvaluator()
            score = await evaluator.evaluate(page.url, config)
            total_score *= score
        elif eval_type == "program_html":
            evaluator = HTMLContentEvaluator()
            score = await evaluator.evaluate(page, config)
            total_score *= score
        else:
            raise ValueError(f"Unknown eval_type: {eval_type}")

    return total_score
