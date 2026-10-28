from __future__ import annotations

import logging

from dailyscraper.execution.context import PageContext
from dailyscraper.pages import AuthPage, AuthTemplate

logger = logging.getLogger(__name__)


class AuthPageScraper:
    def __init__(self, ctx: PageContext, page: AuthPage) -> None:
        self.ctx = ctx
        self.page = page

    def login(self) -> bool:
        for template in self.page.templates:
            self.ctx.pacer.check_interrupted()
            if self._try(template):
                logger.info("Logged in with template %s", template.name)
                return True
            logger.warning("Login with template %s did not succeed", template.name)
        return False

    def _try(self, template: AuthTemplate) -> bool:
        finder = self.ctx.finder
        wait = self.page.waits.login_form
        login = finder.find(template.login_field, wait)
        password = finder.find(template.password_field, wait)
        submit = finder.find(template.submit, wait)
        if login is None or password is None or submit is None:
            return False
        credentials = self.page.credentials
        login.hover()
        login.type(credentials.login)
        self.ctx.pacer.delay(self.page.delays.after_login_entry)
        password.hover()
        password.type(credentials.password)
        self.ctx.pacer.delay(self.page.delays.after_password_entry)
        submit.hover()
        submit.click()
        return finder.find(template.authorized, wait) is not None
