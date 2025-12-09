from fastapi import FastAPI
from pydantic import BaseModel
from typing import Dict, List, Optional
import requests
from bs4 import BeautifulSoup
from urllib.parse import urljoin, urlparse
from collections import deque
import re
from fastapi import Depends, HTTPException, Security
from fastapi.security.api_key import APIKeyHeader
import os

API_KEY_NAME = "X-API-Key"
api_key_header = APIKeyHeader(name=API_KEY_NAME, auto_error=False)

VALID_API_KEYS = set(
    k.strip() for k in os.getenv("API_KEYS", "").split(",") if k.strip()
)

def get_api_key(api_key: str = Security(api_key_header)):
    if api_key in VALID_API_KEYS:
        return api_key
    raise HTTPException(status_code=403, detail="Invalid or missing API key")
# ----------------------------
# FASTAPI APP
# ----------------------------

app = FastAPI(
    title="Persona Crawler API",
    description="Crawl a website and score pages by buyer persona.",
    version="1.0.0"
)

# ----------------------------
# PERSONAS & RECOMMENDATIONS
# ----------------------------

PERSONAS = {
    "Fixer": {
        "positive": ["fast", "instant", "today", "solve", "fix", "guarantee"],
        "negative": ["maybe", "eventually", "could"],
        "proof": ["guarantee", "delivery", "today"]
    },
    "Skeptic": {
        "positive": ["reviews", "trusted", "refund", "returns", "policy"],
        "negative": ["miracle", "hype"],
        "proof": ["reviews", "refund", "returns"]
    },
    "Optimizer": {
        "positive": ["results", "performance", "optimize", "data", "compare"],
        "negative": ["generic"],
        "proof": ["data", "study", "results"]
    },
    "Explorer": {
        "positive": ["discover", "story", "learn", "why"],
        "negative": [],
        "proof": ["community", "story"]
    },
    "DealMax": {
        "positive": ["save", "deal", "bundle", "discount", "off"],
        "negative": [],
        "proof": ["save", "deal", "bundle"]
    }
}

RECOMMENDATIONS = {
    "Fixer": {
        "overall_low": "Add a clear above-the-fold headline stating the problem you solve immediately.",
        "missing_proof": "Add delivery time or a guarantee near the primary CTA.",
        "weak_language": "Use urgency language like 'Get it today' or 'Fast results'.",
        "conflicting_language": "Remove uncertain language like 'may help' or 'eventually'."
    },
    "Skeptic": {
        "overall_low": "Increase visible trust signals above the fold.",
        "missing_proof": "Move reviews, return policy, or trust badges closer to the CTA.",
        "weak_language": "Add reassurance copy such as 'verified buyers' or 'risk-free'.",
        "conflicting_language": "Remove exaggerated or hype-driven claims."
    },
    "Optimizer": {
        "overall_low": "Clarify how this product performs better than alternatives.",
        "missing_proof": "Add comparison tables or data-backed claims.",
        "weak_language": "Use outcome-driven language like 'improves performance'.",
        "conflicting_language": "Reduce generic marketing language."
    },
    "Explorer": {
        "overall_low": "Add storytelling or brand narrative elements.",
        "missing_proof": "Include lifestyle imagery or community usage examples.",
        "weak_language": "Use curiosity-driven language like 'discover why'."
    },
    "DealMax": {
        "overall_low": "Introduce visible savings such as bundles or discounts.",
        "missing_proof": "Show price comparisons or savings badges.",
        "weak_language": "Use deal language like 'Save more' or 'Limited-time offer'."
    }
}

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    )
}

# ----------------------------
# UTILS
# ----------------------------

def clean_text(html: str) -> str:
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()
    text = soup.get_text(separator=" ")
    return re.sub(r"\s+", " ", text).lower()

def count_hits(text: str, words: List[str]) -> int:
    return sum(text.count(w) for w in words)

def score_persona(text: str, persona: Dict) -> Dict:
    pos = count_hits(text, persona["positive"])
    neg = count_hits(text, persona["negative"])
    proof = count_hits(text, persona["proof"])
    raw_score = (pos * 3) + (proof * 5) - (neg * 4)
    score = max(0, min(100, raw_score))
    return {
        "score": score,
        "positive_hits": pos,
        "negative_hits": neg,
        "proof_hits": proof,
    }

def diagnose(score: float, pos: int, neg: int, proof: int) -> List[str]:
    issues = []
    if score < 50:
        issues.append("overall_low")
    if proof == 0:
        issues.append("missing_proof")
    if pos < 2:
        issues.append("weak_language")
    if neg > 0:
        issues.append("conflicting_language")
    return issues

def priority(score: float) -> str:
    if score < 40:
        return "HIGH"
    if score < 60:
        return "MEDIUM"
    return "LOW"

def detect_page_type(url: str) -> str:
    # very simple heuristic
    if url.endswith("/") or url.count("/") <= 3:
        return "Homepage / Landing"
    if "product" in url:
        return "Product Page"
    if "collection" in url or "category" in url:
        return "Collection Page"
    return "Other"

# ----------------------------
# CRAWLER CORE
# ----------------------------

def run_crawl(start_url: str, max_pages: int = 25) -> List[Dict]:
    parsed_start = urlparse(start_url)
    base_domain = parsed_start.netloc.replace("www.", "")

    visited = set()
    queue = deque([start_url])
    pages: List[Dict] = []

    while queue and len(visited) < max_pages:
        url = queue.popleft()
        if url in visited:
            continue

        try:
            resp = requests.get(url, headers=HEADERS, timeout=10)
            if resp.status_code != 200:
                continue
        except Exception:
            continue

        visited.add(url)
        html = resp.text
        text = clean_text(html)

        page_info: Dict = {
            "url": url,
            "page_type": detect_page_type(url),
            "personas": {}
        }

        for pname, persona in PERSONAS.items():
            s = score_persona(text, persona)
            issues = diagnose(s["score"], s["positive_hits"], s["negative_hits"], s["proof_hits"])
            suggestions = []
            if pname in RECOMMENDATIONS:
                for issue in issues:
                    if issue in RECOMMENDATIONS[pname]:
                        suggestions.append(RECOMMENDATIONS[pname][issue])

            page_info["personas"][pname] = {
                "score": s["score"],
                "priority": priority(s["score"]),
                "issues": issues,
                "suggestions": suggestions
            }

        pages.append(page_info)

        # discover new links
        soup = BeautifulSoup(html, "html.parser")
        for a in soup.find_all("a", href=True):
            link = urljoin(url, a["href"])
            parsed_link = urlparse(link)
            link_domain = parsed_link.netloc.replace("www.", "")

            if link_domain == "" or link_domain == base_domain:
                if link not in visited:
                    queue.append(link)

    return pages

# ----------------------------
# REQUEST / RESPONSE MODELS
# ----------------------------

class CrawlRequest(BaseModel):
    url: str
    max_pages: int = 25

class PersonaScore(BaseModel):
    score: float
    priority: str
    issues: List[str]
    suggestions: List[str]

class PageResult(BaseModel):
    url: str
    page_type: str
    personas: Dict[str, PersonaScore]

class CrawlResponse(BaseModel):
    start_url: str
    max_pages: int
    pages: List[PageResult]

# ----------------------------
# API ENDPOINT
# ----------------------------

@app.post("/crawl", response_model=CrawlResponse)
def crawl_site(req: CrawlRequest, api_key: str = Depends(get_api_key)):
    pages_raw = run_crawl(req.url, req.max_pages)
    # FastAPI + Pydantic will validate/convert automatically
    return CrawlResponse(
        start_url=req.url,
        max_pages=req.max_pages,
        pages=pages_raw
    )

@app.get("/")
def root():
    return {
        "message": "Persona Crawler API is running.",
        "endpoints": {
            "POST /crawl": "Run a persona-based crawl on a website."
        }
    }
