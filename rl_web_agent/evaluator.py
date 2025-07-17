"""Simplified evaluator for RL Web Agent - no WebArena dependencies"""

import html
import time
from typing import Any


class StringEvaluator:
    """Check whether the answer is correct with exact match or must include criteria"""

    @staticmethod
    def clean_answer(answer: str) -> str:
        """Clean and normalize answer string"""
        answer = answer.strip()
        if answer.startswith("'") and answer.endswith("'"):
            answer = answer[1:-1]
        elif answer.startswith('"') and answer.endswith('"'):
            answer = answer[1:-1]
        return answer.lower()

    @staticmethod
    def exact_match(ref: str, pred: str) -> float:
        """Check if prediction exactly matches reference"""
        return float(StringEvaluator.clean_answer(pred) == StringEvaluator.clean_answer(ref))

    @staticmethod
    def must_include(ref: str, pred: str) -> float:
        """Check if prediction includes reference text"""
        clean_ref = StringEvaluator.clean_answer(ref)
        clean_pred = StringEvaluator.clean_answer(pred)
        return float(clean_ref in clean_pred)

    def evaluate(self, answer: str, config: dict[str, Any]) -> float:
        """Evaluate answer against reference answers in config"""
        score = 1.0
        reference_answers = config["eval"]["reference_answers"]

        for approach, value in reference_answers.items():
            if approach == "exact_match":
                score *= self.exact_match(ref=value, pred=answer)
            elif approach == "must_include":
                if isinstance(value, list):
                    for must_value in value:
                        score *= self.must_include(ref=must_value, pred=answer)
                else:
                    score *= self.must_include(ref=value, pred=answer)

        return score


class URLEvaluator:
    """Check URL matching"""

    def evaluate(self, page_url: str, config: dict[str, Any]) -> float:
        """Evaluate current page URL against reference URL"""

        def clean_url(url: str) -> str:
            return str(url).rstrip("/")

        reference_url = config["eval"]["reference_url"]
        if not reference_url:
            return 1.0  # No URL requirement

        pred_url = clean_url(page_url)
        ref_url = clean_url(reference_url)

        # Simple contains check - can be made more sophisticated
        return float(ref_url in pred_url or pred_url in ref_url)


class HTMLContentEvaluator:
    """Check whether specific content appears in the page"""

    def evaluate(self, page, config: dict[str, Any]) -> float:
        """Evaluate page content against required content"""
        targets = config["eval"].get("program_html", [])
        if not targets:
            return 1.0  # No HTML content requirements

        score = 1.0
        for target in targets:
            target_url = target.get("url", "last")
            locator = target.get("locator", "")

            # Navigate to target URL if needed
            if target_url != "last":
                page.goto(target_url)
                time.sleep(1)  # Brief wait for page load

            # Get page content
            if not locator.strip():
                # Use full page content
                selected_element = page.content()
            elif locator.startswith("document."):
                # Use JavaScript to select element
                try:
                    selected_element = str(page.evaluate(f"() => {locator}"))
                    if not selected_element:
                        selected_element = ""
                except Exception:
                    selected_element = ""
            else:
                # For other locators, use full page content as fallback
                selected_element = page.content()

            selected_element = html.unescape(selected_element)

            # Check required content
            required_contents = target.get("required_contents", {})
            if "exact_match" in required_contents:
                required = required_contents["exact_match"]
                cur_score = StringEvaluator.exact_match(ref=required, pred=selected_element)
                score *= float(cur_score)
            elif "must_include" in required_contents:
                required_list = required_contents["must_include"]
                if isinstance(required_list, list):
                    for content in required_list:
                        cur_score = StringEvaluator.must_include(ref=content, pred=selected_element)
                        score *= float(cur_score)
                else:
                    cur_score = StringEvaluator.must_include(ref=required_list, pred=selected_element)
                    score *= float(cur_score)

        return score


def evaluate_task(answer: str, page, config: dict[str, Any]) -> float:
    """
    Evaluate a task using the provided answer, page, and configuration.

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
            score = evaluator.evaluate(answer, config)
            total_score *= score
        elif eval_type == "url_match":
            evaluator = URLEvaluator()
            score = evaluator.evaluate(page.url, config)
            total_score *= score
        elif eval_type == "program_html":
            evaluator = HTMLContentEvaluator()
            score = evaluator.evaluate(page, config)
            total_score *= score
        else:
            raise ValueError(f"Unknown eval_type: {eval_type}")

    return total_score
