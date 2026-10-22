from __future__ import annotations

from pydantic import Field

from dailyscraper.pages.base import DelayRange, FrozenModel, Seconds
from dailyscraper.pages.selectors import Selector
from dailyscraper.pages.state import PageStateRules


class Credentials(FrozenModel):
    login: str = Field(min_length=1)
    password: str = Field(min_length=1, repr=False)


class AuthTemplate(FrozenModel):
    name: str = Field(min_length=1)
    ready: Selector | None = None
    login_field: Selector
    password_field: Selector
    submit: Selector
    authorized: Selector


class AuthWaits(FrozenModel):
    page: Seconds = 1
    login_form: Seconds = 120


class AuthDelays(FrozenModel):
    after_login_entry: DelayRange = (0, 0)
    after_password_entry: DelayRange = (0, 0)


class AuthPage(FrozenModel):
    credentials: Credentials
    templates: tuple[AuthTemplate, ...] = Field(min_length=1)
    waits: AuthWaits = AuthWaits()
    delays: AuthDelays = AuthDelays()
    state: PageStateRules | None = None
