import csv
import hashlib
import json
import logging
import random
import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from io import StringIO
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup

_LOGGER = logging.getLogger(__name__)

BASE_URL = "https://myaccount.esbnetworks.ie"
LOGIN_BASE = "https://login.esbnetworks.ie"
B2C_TENANT = "esbntwkscustportalprdb2c01.onmicrosoft.com"
B2C_POLICY = "B2C_1A_signup_signin"

# ESB CSV timestamps are Irish wall-clock time and mark the END of each
# 30-minute interval.
ESB_TZ = ZoneInfo("Europe/Dublin")
INTERVAL = timedelta(minutes=30)

# After ESB blocks (CAPTCHA) or rejects a login, don't try again for this long.
# ESB allows roughly two logins per IP per day, so hammering only extends the block.
LOGIN_BACKOFF = timedelta(hours=24)

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:125.0) Gecko/20100101 Firefox/125.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_4_1) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4.1 Safari/605.1.15",
]


class ESBError(Exception):
    """Generic failure talking to ESB Networks."""


class ESBAuthError(ESBError):
    """ESB rejected the username/password."""


class ESBCaptchaError(ESBError):
    """ESB demanded human verification (rate limited)."""


class ESBLoginBackoff(ESBError):
    """A recent login failed, so we are deliberately not trying again yet."""


def _sleep(lo=1.5, hi=4.0):
    time.sleep(random.uniform(lo, hi))


class ESBNetworksAPI:
    def __init__(self, username: str, password: str, mprn: str, cache_path: str):
        self._username = username
        self._password = password
        self._mprn = mprn.replace(" ", "").strip()
        self._cache_path = cache_path
        self._session = None
        self._ua = random.choice(USER_AGENTS)

    def _base_headers(self):
        return {
            "User-Agent": self._ua,
            "Accept-Language": "en-IE,en;q=0.9",
            "Accept-Encoding": "gzip, deflate, br",
        }

    @property
    def _credentials_hash(self) -> str:
        return hashlib.sha256(f"{self._username}\0{self._password}".encode()).hexdigest()

    # --- cache file -------------------------------------------------------

    def _read_cache(self) -> dict:
        try:
            with open(self._cache_path) as f:
                return json.load(f)
        except Exception:
            return {}

    def _write_cache(self, data: dict) -> None:
        try:
            with open(self._cache_path, "w") as f:
                json.dump(data, f)
        except Exception as e:
            _LOGGER.warning("ESB: could not write session cache: %s", e)

    def _save_session(self, session: requests.Session):
        self._write_cache({"cookies": dict(session.cookies), "saved_at": time.time()})
        _LOGGER.info("ESB: session cached")

    def _record_login_failure(self, reason: str) -> None:
        data = self._read_cache()
        data["login_failure"] = {
            "at": time.time(),
            "reason": reason,
            "credentials": self._credentials_hash,
        }
        self._write_cache(data)

    def _check_backoff(self) -> None:
        """Raise ESBLoginBackoff if a login with these credentials failed recently."""
        failure = self._read_cache().get("login_failure")
        if not failure or failure.get("credentials") != self._credentials_hash:
            return
        retry_at = failure.get("at", 0) + LOGIN_BACKOFF.total_seconds()
        if time.time() < retry_at:
            retry = datetime.fromtimestamp(retry_at, tz=timezone.utc)
            raise ESBLoginBackoff(
                f"last login failed ({failure.get('reason')}); "
                f"not retrying until {retry.isoformat(timespec='minutes')}"
            )

    def _validate_session(self, cookies: dict) -> bool:
        """Check if cached session cookies still work."""
        try:
            s = requests.Session()
            s.headers.update(self._base_headers())
            for k, v in cookies.items():
                s.cookies.set(k, v)
            # GET portal page — returns 200 if authenticated, 302 if session expired
            r = s.get(f"{BASE_URL}/Api/HistoricConsumption", timeout=15, allow_redirects=False)
            if r.status_code == 200:
                _LOGGER.info("ESB: cached session is valid")
                self._session = s
                return True
            _LOGGER.info("ESB: cached session no longer valid (status %s)", r.status_code)
        except Exception as e:
            _LOGGER.warning("ESB: session validation error: %s", e)
        return False

    # --- login --------------------------------------------------------------

    def login(self) -> None:
        """Perform the full Azure B2C login flow, honouring the CAPTCHA/rejection backoff.

        Raises ESBAuthError, ESBCaptchaError, ESBLoginBackoff or ESBError.
        """
        self._check_backoff()
        try:
            self._login()
        except ESBCaptchaError:
            self._record_login_failure("captcha")
            raise
        except ESBAuthError:
            self._record_login_failure("rejected")
            raise
        # Other failures (network blips, page changes) are retried next update.

    def _login(self) -> None:
        _LOGGER.info("ESB: starting login flow")
        s = requests.Session()
        s.headers.update(self._base_headers())

        # Step 1 — GET homepage, follow redirect to B2C login, extract SETTINGS
        try:
            r1 = s.get(BASE_URL, timeout=20)
            r1.raise_for_status()
        except Exception as e:
            raise ESBError(f"step 1 failed: {e}") from e

        settings_match = re.findall(r"(?<=var SETTINGS = )\S*;", r1.text)
        if not settings_match:
            raise ESBError("could not find SETTINGS in login page")
        try:
            settings = json.loads(settings_match[0].rstrip(";"))
        except Exception as e:
            raise ESBError(f"could not parse SETTINGS JSON: {e}") from e

        csrf = settings.get("csrf")
        trans_id = settings.get("transId")
        if not csrf or not trans_id:
            raise ESBError("missing csrf or transId in SETTINGS")

        _sleep()

        # Step 2 — POST credentials to SelfAsserted
        self_asserted_url = (
            f"{LOGIN_BASE}/{B2C_TENANT}/{B2C_POLICY}/SelfAsserted"
            f"?tx={trans_id}&p={B2C_POLICY}"
        )
        try:
            r2 = s.post(
                self_asserted_url,
                data={
                    "signInName": self._username,
                    "password": self._password,
                    "request_type": "RESPONSE",
                },
                headers={
                    "x-csrf-token": csrf,
                    "X-Requested-With": "XMLHttpRequest",
                    "Origin": LOGIN_BASE,
                    "Referer": r1.url,
                    "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
                    "Accept": "application/json, text/javascript, */*; q=0.01",
                },
                allow_redirects=False,
                timeout=20,
            )
        except Exception as e:
            raise ESBError(f"step 2 (SelfAsserted) failed: {e}") from e

        try:
            r2_json = r2.json()
        except Exception:
            r2_json = {}
        if str(r2_json.get("status")) != "200":
            message = str(r2_json.get("message", ""))
            if _looks_like_captcha(message) or _looks_like_captcha(r2.text):
                raise ESBCaptchaError("CAPTCHA required at sign-in")
            raise ESBAuthError(f"login rejected: {message or r2_json or r2.status_code}")

        _sleep()

        # Step 3 — GET confirmed page, extract hidden form
        confirmed_url = (
            f"{LOGIN_BASE}/{B2C_TENANT}/{B2C_POLICY}/api/CombinedSigninAndSignup/confirmed"
            f"?rememberMe=false&csrf_token={csrf}&tx={trans_id}&p={B2C_POLICY}"
        )
        try:
            r3 = s.get(confirmed_url, timeout=20)
            r3.raise_for_status()
        except Exception as e:
            raise ESBError(f"step 3 (confirmed) failed: {e}") from e

        if _looks_like_captcha(r3.text):
            raise ESBCaptchaError("CAPTCHA detected — rate limit hit")

        soup3 = BeautifulSoup(r3.text, "html.parser")
        form = soup3.find("form", {"id": "auto"})
        if not form:
            raise ESBError("could not find auto-submit form in confirmed page")

        action_url = form.get("action", "")
        form_data = {inp.get("name"): inp.get("value", "") for inp in form.find_all("input") if inp.get("name")}

        _sleep()

        # Step 4 — POST to signin-oidc
        try:
            s.post(
                action_url,
                data=form_data,
                headers={
                    "Origin": LOGIN_BASE,
                    "Referer": r3.url,
                    "Content-Type": "application/x-www-form-urlencoded",
                },
                allow_redirects=False,
                timeout=20,
            )
        except Exception as e:
            raise ESBError(f"step 4 (signin-oidc) failed: {e}") from e

        _sleep()

        # Step 5 — GET myaccount homepage to finalise session
        try:
            r5 = s.get(BASE_URL, headers={"Referer": LOGIN_BASE}, timeout=20)
            r5.raise_for_status()
        except Exception as e:
            raise ESBError(f"step 5 (homepage finalise) failed: {e}") from e

        _sleep(0.5, 1.5)

        _LOGGER.info("ESB: login successful")
        self._session = s
        self._save_session(s)

    def _get_xsrf_token(self) -> str | None:
        try:
            r = self._session.get(
                f"{BASE_URL}/af/t",
                headers={
                    "X-Returnurl": f"{BASE_URL}/Api/HistoricConsumption",
                    "Referer": f"{BASE_URL}/Api/HistoricConsumption",
                    "Accept": "*/*",
                },
                timeout=15,
            )
            r.raise_for_status()
            return r.json().get("token")
        except Exception as e:
            _LOGGER.warning("ESB: failed to get XSRF token: %s", e)
            return None

    def download_csv(self) -> str:
        """Ensure authenticated, then POST to DownloadHdfPeriodic for CSV data.

        A cached session is reused for as long as ESB accepts it; a fresh login
        only happens when it has expired. Raises an ESBError subclass on failure.
        """
        cookies = self._read_cache().get("cookies")
        if not (cookies and self._validate_session(cookies)):
            self.login()

        token = self._get_xsrf_token()
        if not token:
            # Session may have expired mid-flight — try fresh login
            _LOGGER.warning("ESB: no XSRF token, retrying with fresh login")
            self.login()
            token = self._get_xsrf_token()
        if not token:
            raise ESBError("could not get XSRF token after fresh login")

        _sleep(0.5, 1.5)

        try:
            r = self._session.post(
                f"{BASE_URL}/DataHub/DownloadHdfPeriodic",
                json={"mprn": self._mprn, "searchType": "intervalkwh"},
                headers={
                    "X-Xsrf-Token": token,
                    "X-Returnurl": f"{BASE_URL}/Api/HistoricConsumption",
                    "Referer": f"{BASE_URL}/Api/HistoricConsumption",
                    "Origin": BASE_URL,
                    "Content-Type": "application/json",
                },
                timeout=60,
            )
            r.raise_for_status()
        except Exception as e:
            raise ESBError(f"CSV download failed: {e}") from e

        if len(r.content) < 100:
            raise ESBError(f"response too short ({len(r.content)} bytes): {r.text[:200]}")

        _LOGGER.info("ESB: downloaded %d bytes (ct=%s)", len(r.content), r.headers.get("Content-Type", ""))
        return r.text


def _looks_like_captcha(text: str) -> bool:
    text = text.lower()
    return "captcha" in text or "robot" in text


@dataclass
class ParsedData:
    """Half-hourly readings as {start: aware UTC datetime, kwh: float}, sorted."""

    imports: list[dict] = field(default_factory=list)
    exports: list[dict] = field(default_factory=list)


def _interval_start(end_local: datetime, fold: int) -> datetime:
    """Convert a naive Irish end-of-interval time to the interval's UTC start."""
    return end_local.replace(tzinfo=ESB_TZ, fold=fold).astimezone(timezone.utc) - INTERVAL


def parse_csv(csv_text: str) -> ParsedData:
    """Parse an ESB interval CSV into import (consumption) and export readings."""
    rows = []
    for row in csv.DictReader(StringIO(csv_text)):
        try:
            read_type = row.get("Read Type", "").strip()
            if "Active Import" in read_type:
                kind = "imports"
            elif "Active Export" in read_type:
                kind = "exports"
            else:
                continue
            dt_str = row.get("Read Date and End Time", "").strip()
            value_str = row.get("Read Value", "").strip()
            if not dt_str or not value_str:
                continue
            rows.append((kind, datetime.strptime(dt_str, "%d-%m-%Y %H:%M"), float(value_str)))
        except (ValueError, KeyError):
            continue

    # When clocks go back, 01:00-02:00 Irish time happens twice and ESB lists the
    # same wall-clock time twice. Give the chronologically second one fold=1.
    # ESB files are usually newest-first, so work out the file's direction.
    descending = len(rows) > 1 and rows[0][1] > rows[-1][1]
    seen: dict[tuple[str, datetime], int] = {}
    data = ParsedData()
    for kind, end_local, kwh in (reversed(rows) if descending else rows):
        occurrence = seen.get((kind, end_local), 0)
        seen[(kind, end_local)] = occurrence + 1
        getattr(data, kind).append(
            {"start": _interval_start(end_local, fold=min(occurrence, 1)), "kwh": kwh}
        )

    data.imports.sort(key=lambda x: x["start"])
    data.exports.sort(key=lambda x: x["start"])
    _LOGGER.info(
        "ESB: parsed %d import and %d export datapoints from CSV",
        len(data.imports), len(data.exports),
    )
    return data
