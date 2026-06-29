"""Domain entity types for Graphiti knowledge-graph extraction.

Each class describes a category of real-world entity the graph can recognise.
Graphiti uses these as extraction hints when ingesting an episode — the
docstrings become part of the prompt that guides the LLM classifier.
"""
from pydantic import BaseModel


class Person(BaseModel):
    """A human being — the user themselves, a colleague, friend, family member, or public figure."""


class Organization(BaseModel):
    """A company, institution, team, government body, or any named group of people."""


class Place(BaseModel):
    """A geographic location — country, city, region, landmark, or physical place."""


class Concept(BaseModel):
    """An abstract idea, technology, framework, methodology, skill, or domain of knowledge."""


class Event(BaseModel):
    """A specific occurrence — a meeting, project, deadline, or milestone."""


class Preference(BaseModel):
    """A preference, taste, like, dislike, or habitual behavior of the user."""


class Fact(BaseModel):
    """A factual statement, belief, or piece of information about the user or their world."""


ENTITY_TYPES: dict[str, type[BaseModel]] = {
    "Person":       Person,
    "Organization": Organization,
    "Place":        Place,
    "Concept":      Concept,
    "Event":        Event,
    "Preference":   Preference,
    "Fact":         Fact,
}
