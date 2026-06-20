"""DataForSEO Keywords Data API — Google search volume / competition / CPC."""
import requests
import logging

logger = logging.getLogger(__name__)

BASE = "https://api.dataforseo.com/v3"


class DataForSEOClient:
    def __init__(self, login, password):
        self.login = login
        self.password = password

    def _post(self, path, body):
        url = f"{BASE}{path}"
        try:
            resp = requests.post(url, json=body, auth=(self.login, self.password), timeout=30)
            return resp.json()
        except Exception as e:
            logger.error(f"DataForSEO API error: {e}")
            return {"status_code": 500, "error": str(e)}

    def search_volume(self, keywords, location_code=2840, language_code="en"):
        """Batch query Google search volume.
        Returns dict: {keyword: {search_volume, competition, cpc}} or {} on failure.
        """
        if not keywords:
            return {}
        result = {}
        # DataForSEO limits 300 keywords per request
        for batch in [keywords[i:i + 200] for i in range(0, len(keywords), 200)]:
            body = [{"keywords": batch, "location_code": location_code, "language_code": language_code}]
            resp = self._post("/keywords_data/google/search_volume/live", body)
            tasks = resp.get("tasks") or []
            for task in (tasks or []):
                for r in (task.get("result") or []):
                    kw = r.get("keyword", "")
                    result[kw] = {
                        "search_volume": r.get("search_volume") or 0,
                        "competition": r.get("competition") or 0,
                        "cpc": r.get("cpc") or 0,
                    }
        return result


def compute_hotness(volume, competition, cpc):
    """Compute a composite hotness score 0-100."""
    vol_score = min(volume / 1000 * 3, 40) if volume else 0
    comp_score = (competition or 0) * 30
    cpc_score = min((cpc or 0) * 10, 30)
    return round(min(vol_score + comp_score + cpc_score, 100))
