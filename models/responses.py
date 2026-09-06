from pydantic import BaseModel, Field
from typing import Any, Dict, Literal, List, Optional
from datetime import datetime, timezone


class PingResponse(BaseModel):
    status: Literal["ok", "error"] = Field("ok", description="API status")
    message: str = Field("Controller API is running", description="Status message")


class BrowserResponse(BaseModel):
    browser_id: str
    profile_path: Optional[str]
    session_count: int


class NetworkTraceQueryParameter(BaseModel):
    name: str
    value: str


class NetworkTraceEntry(BaseModel):
    url: str
    query_parameter_names: List[str] = Field(default_factory=list)
    query_parameters: List[NetworkTraceQueryParameter] = Field(default_factory=list)
    method: str
    resource_type: str
    is_navigation_request: bool
    frame_url: str
    redirected_from_url: Optional[str] = None
    request_headers: Dict[str, str] = Field(default_factory=dict)
    post_data: Optional[str] = None
    post_data_base64: Optional[str] = None
    request_timing: Dict[str, float] = Field(default_factory=dict)
    request_sizes: Optional[Dict[str, int]] = None
    status: Optional[int] = None
    status_text: Optional[str] = None
    content_type: Optional[str] = None
    response_headers: Dict[str, str] = Field(default_factory=dict)
    from_service_worker: Optional[bool] = None
    server_addr: Optional[Dict[str, Any]] = None
    security_details: Optional[Dict[str, Any]] = None
    failure: Optional[str] = None
    started_ms: float
    duration_ms: Optional[float] = None


class NetworkTraceSnapshot(BaseModel):
    entries: List[NetworkTraceEntry] = Field(default_factory=list)
    dropped: int = 0


class ContentWithNetworkTraceResponse(BaseModel):
    content: str
    content_type: str
    content_encoding: Literal["utf-8", "base64"]
    final_url: str
    navigation_status: Optional[int] = None
    network_trace: NetworkTraceSnapshot


class RatingMetadata(BaseModel):
    rating: Optional[float] = Field(None, description="Rating value (e.g. 4.9)")
    reviews: Optional[int] = Field(None, description="Number of reviews")
    description: Optional[str] = Field(None, description="Full rating description")


class SearchMetadata(BaseModel):
    rating: Optional[RatingMetadata] = None
    thumbnail: Optional[str] = Field(None, description="Base64-encoded thumbnail data URL")


class SearchResult(BaseModel):
    link: str
    title: str
    snippet: str
    favicon: Optional[str] = Field(None, description="Base64-encoded favicon data URL")
    metadata: Optional[SearchMetadata] = None


class WikiResult(BaseModel):
    desc: str = Field(..., description="Combined text from wiki panel")
    wiki_links: List[str] = Field(default_factory=list)
    related_links: List[str] = Field(default_factory=list)
    misc_links: List[str] = Field(default_factory=list)


class AiReview(BaseModel):
    summary: str
    sources: List[str]


class SearchResponse(BaseModel):
    ai_review: Optional[AiReview] = None
    wiki: Optional[WikiResult] = None
    results: List[SearchResult]


class NewsResult(BaseModel):
    link: str
    title: str
    snippet: str
    favicon: Optional[str] = Field(None, description="Base64-encoded favicon data URL")
    thumbnail: Optional[str] = Field(None, description="Base64-encoded thumbnail data URL")


class NewsResponse(BaseModel):
    results: List[NewsResult]


class MediaTags(BaseModel):
    source: Optional[str] = None
    author: Optional[str] = None
    date: Optional[str] = None


class MediaResult(BaseModel):
    link: str
    title: str
    icon: Optional[str] = Field(None, description="Base64-encoded icon/thumbnail data URL")
    tags: Optional[MediaTags] = None
    search_date: datetime = Field(default_factory=lambda: datetime.now(timezone.utc), description="Datetime when the result was parsed")


class ImagesResponse(BaseModel):
    results: List[MediaResult]


class VideosResponse(BaseModel):
    results: List[MediaResult]


class SearchHintsResponse(BaseModel):
    query: str = Field(..., description="The original query")
    hints: List[str] = Field(default_factory=list, description="List of autocomplete suggestions")
