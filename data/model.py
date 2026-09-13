from pydantic import BaseModel,Field
from typing import Literal,Any

class Constant(BaseModel):
    name:str
    description:str
    value:str|int

class Variable(BaseModel):
    name:str
    description:str
    type:Literal["",]
    size:str | int |None=None
    element_type:str | None = None
    options:list[Any] = Field()
    initial_value:list[Any] = Field(...)

class Process(BaseModel):
    name:str
    description:str
    parameters:list[Any] = Field(default_factory=list)

class Transition(BaseModel):
    process:Process
    guard:str= Field(...)
    event:str= Field(...)
    effects:list[Any] = Field(default_factory=list)
    next_process:Process

class System(BaseModel):
    name:str
    description:str
    composition:Literal["single","interleave","parallel","interrupt"]
    processes:list[Process] = Field(...)

class Predicates(BaseModel):
    name:str
    description:str
    expressions:str


class Assertion(BaseModel):
    description: str
    type: Literal["deadlock_free", "reachability", "ltl"]
    process: str
    predicate: str | None = None
    formula: str | None = None
    expected: Literal["VALID", "INVALID"]

class CSP(BaseModel):
    constants:list[Constant]
    variables:list[Variable]
    transitions:list[Transition]
    assertions:list[Assertion]
    system:System
    predicates:list[Predicates]
    process:list[Process]




