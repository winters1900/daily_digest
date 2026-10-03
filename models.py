from dataclasses import dataclass, field
from datetime import datetime
from typing import List, Optional


@dataclass
class Article:
    title: str
    url: str
    score: int
    snippet: str
    source: str          # stackoverflow | github | reddit | substack
    score_label: str     # ▲42  /  ⭐1.2k  /  🔥256  /  📰
    tags: List[str] = field(default_factory=list)
    extra: str = ""      # subreddit / publication name / language
    published: Optional[datetime] = None
