import hashlib
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, field_validator


CompactItem = Annotated[str, Field(min_length=1, max_length=160)]


class JobPosting(BaseModel):
    id: str
    title: str
    company: str
    url: str
    posted_date: str | None = None

    def composite_id(self) -> str:
        value = f"{self.company} | {self.title} | {self.url}"
        return hashlib.sha256(value.encode("utf-8")).hexdigest()


class JobDetails(BaseModel):
    description_text: str = Field(min_length=1)
    location: str | None = Field(default=None, max_length=200)
    work_arrangement: str | None = Field(default=None, max_length=100)
    employment_type: str | None = Field(default=None, max_length=100)
    salary: str | None = Field(default=None, max_length=200)

    @field_validator("description_text", mode="before")
    @classmethod
    def strip_description(cls, value: object) -> object:
        if isinstance(value, str):
            return value.strip()
        return value

    @field_validator(
        "location",
        "work_arrangement",
        "employment_type",
        "salary",
        mode="before",
    )
    @classmethod
    def strip_optional_text(cls, value: object) -> object:
        if isinstance(value, str):
            return value.strip() or None
        return value


class JobSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    overview: str = Field(min_length=1, max_length=280)
    responsibilities: list[CompactItem] = Field(default_factory=list, max_length=3)
    requirements: list[CompactItem] = Field(default_factory=list, max_length=3)
    tech_stack: list[CompactItem] = Field(default_factory=list, max_length=8)
    experience: str | None = Field(default=None, max_length=160)
    location: str | None = Field(default=None, max_length=160)
    work_arrangement: str | None = Field(default=None, max_length=80)
    employment_type: str | None = Field(default=None, max_length=80)
    salary: str | None = Field(default=None, max_length=160)

    @field_validator(
        "overview",
        "experience",
        "location",
        "work_arrangement",
        "employment_type",
        "salary",
        mode="before",
    )
    @classmethod
    def strip_summary_text(cls, value: object) -> object:
        if isinstance(value, str):
            return value.strip()
        return value

    @field_validator("responsibilities", "requirements", "tech_stack", mode="before")
    @classmethod
    def strip_list_items(cls, value: object) -> object:
        if isinstance(value, list):
            return [item.strip() if isinstance(item, str) else item for item in value]
        return value


class JobAlert(BaseModel):
    posting: JobPosting
    details: JobDetails | None = None
    summary: JobSummary | None = None
    summary_unavailable: bool = False
