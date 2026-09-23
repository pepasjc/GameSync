"""GameSync server client for the MiSTer, on urllib alone.

MiSTer has no pip, so there is no requests here. Only the endpoints this client
actually needs are implemented.

Which transport a save uses is a per-system rule, not a preference: PS1 saves
are raw memory cards and go through ``/ps1-card`` so they land in the same
server slot as every other PS1 client, and everything else uses ``/raw``.
"""

from __future__ import annotations

import json
import socket
import urllib.error
import urllib.parse
import urllib.request

DEFAULT_TIMEOUT = 20
UPLOAD_TIMEOUT = 120
DOWNLOAD_TIMEOUT = 120
#: A rescan walks the whole ROM library on the server before answering.
RESCAN_TIMEOUT = 300

#: Systems whose saves are memory cards handled by the dedicated endpoints.
CARD_SYSTEMS = {"PS1"}


class ApiError(RuntimeError):
    """A request failed. ``status`` is the HTTP code when there was one."""

    def __init__(self, message, status=0):
        RuntimeError.__init__(self, message)
        self.status = status


class Client:
    def __init__(self, base_url: str, api_key: str = "",
                 timeout: int = DEFAULT_TIMEOUT):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.timeout = timeout

    # ---------------------------------------------------------------- plumbing

    def _request(self, method, path, body=None, content_type=None,
                 timeout=None, raw_response=False, query=None):
        url = self.base_url + path
        if query:
            url += "?" + urllib.parse.urlencode(query)
        request = urllib.request.Request(url, data=body, method=method)
        if self.api_key:
            request.add_header("X-API-Key", self.api_key)
        if content_type:
            request.add_header("Content-Type", content_type)

        try:
            response = urllib.request.urlopen(
                request, timeout=timeout or self.timeout)
        except urllib.error.HTTPError as exc:
            detail = ""
            try:
                detail = exc.read().decode("utf-8", "replace")[:200]
            except Exception:
                pass
            raise ApiError("%s %s -> %d %s" % (method, path, exc.code, detail),
                           status=exc.code)
        except urllib.error.URLError as exc:
            raise ApiError("%s %s -> %s" % (method, path, exc.reason))
        except socket.timeout:
            raise ApiError("%s %s -> timed out" % (method, path))

        with response:
            payload = response.read()
            if raw_response:
                return payload, dict(response.headers)
        if not payload:
            return {}
        try:
            return json.loads(payload.decode("utf-8"))
        except ValueError:
            raise ApiError("%s %s -> malformed JSON" % (method, path))

    def _get_json(self, path, query=None, timeout=None):
        return self._request("GET", path, query=query, timeout=timeout)

    def _post_json(self, path, payload, timeout=None):
        body = json.dumps(payload).encode("utf-8")
        return self._request("POST", path, body=body,
                             content_type="application/json", timeout=timeout)

    # ----------------------------------------------------------------- calls

    def status(self):
        """Server health. The only endpoint that needs no API key."""
        return self._get_json("/status")

    def sync_plan(self, titles, console_id="", platforms=None):
        """One round trip that classifies every title.

        Returns the server's plan: which titles to upload, download, which
        conflict, which are already up to date, and which exist only on the
        server.
        """
        payload = {"titles": titles}
        if console_id:
            payload["console_id"] = console_id
        if platforms:
            payload["platforms"] = sorted(platforms)
        return self._post_json("/sync", payload, timeout=60)

    def download_save(self, title_id, system=""):
        """Save bytes for a title, or None when the server has none."""
        path = self._save_path(title_id, system)
        try:
            payload, _headers = self._request(
                "GET", path, raw_response=True, timeout=DOWNLOAD_TIMEOUT)
        except ApiError as exc:
            if exc.status == 404:
                return None
            raise
        return payload

    def upload_save(self, title_id, data, system="", console_id="",
                    game_name=""):
        """Push save bytes. ``force`` avoids a spurious timestamp conflict."""
        query = {"force": "true"}
        if console_id:
            query["console_id"] = console_id
        if game_name:
            query["game_name"] = game_name
        path = self._save_path(title_id, system)
        return self._request("POST", path, body=data,
                             content_type="application/octet-stream",
                             query=query, timeout=UPLOAD_TIMEOUT)

    @staticmethod
    def _save_path(title_id, system=""):
        quoted = urllib.parse.quote(str(title_id), safe="")
        if (system or "").upper() in CARD_SYSTEMS:
            return "/saves/%s/ps1-card" % quoted
        return "/saves/%s/raw" % quoted

    def update_names(self, names):
        """Share resolved game names so the server can label a title."""
        if not names:
            return {}
        return self._post_json("/titles/update_names", {"names": names})

    def card_meta(self, title_id, slot=0):
        """Raw-card metadata for a memory-card title, or None.

        The generic ``/titles`` index reports the hash of the *stored bundle*
        for a card save, which is never what a client holding a raw 128 KB card
        computes. Comparing against that makes every card look perpetually out
        of date, so card systems compare against this endpoint instead.
        """
        quoted = urllib.parse.quote(str(title_id), safe="")
        try:
            return self._get_json("/saves/%s/ps1-card/meta" % quoted,
                                  query={"slot": slot})
        except ApiError as exc:
            if exc.status == 404:
                return None
            raise

    def list_titles(self):
        """Everything the server holds: ``{title_id: metadata}``."""
        body = self._get_json("/titles", timeout=60)
        titles = body.get("titles", []) if isinstance(body, dict) else body
        return {str(entry.get("title_id")): entry
                for entry in titles or [] if entry.get("title_id")}

    def rescan_roms(self):
        """Ask the server to walk its ROM folder again.

        The server's catalogue lives in memory and only moves on a scan, so
        a file added to its disk is invisible until the periodic scan runs.
        Returns the row count, or None when the server refused (a non-admin
        user behind a proxy) - the caller carries on with what it has.
        """
        try:
            body = self._get_json("/roms/scan", timeout=RESCAN_TIMEOUT)
        except ApiError as exc:
            if exc.status in (403, 404, 405):
                return None
            raise
        return int(body.get("count") or 0) if isinstance(body, dict) else None

    def rom_fingerprints(self):
        """``{system: {"fingerprint", "count"}}``, or None on a server that
        predates the endpoint - then the catalogue is fetched whole."""
        try:
            body = self._get_json("/roms/fingerprints", timeout=30)
        except ApiError as exc:
            if exc.status in (404, 405):
                return None
            raise
        systems = body.get("systems") if isinstance(body, dict) else None
        return systems if isinstance(systems, dict) else None

    #: One page of the catalogue. The server allows up to 20000 rows in a
    #: single response, but a page that large is a multi-megabyte JSON blob to
    #: parse on a 492 MB device, so it is fetched in smaller bites.
    ROM_PAGE_SIZE = 2000

    #: Safety net for a runaway loop; far above any real catalogue.
    MAX_ROM_PAGES = 200

    def list_roms(self, system="", progress=None, fields=None):
        """Every catalogue row, following pagination to the end.

        The catalogue is ordered by ``title_id``, which starts with the system
        code - so a single truncated page silently loses every system after the
        cut-off alphabetically. Anything past 20000 ROMs must be paged or whole
        systems appear empty.
        """
        roms = []
        offset = 0
        for _page in range(self.MAX_ROM_PAGES):
            query = {"limit": self.ROM_PAGE_SIZE, "offset": offset}
            if system:
                query["system"] = system
            body = self._get_json("/roms", query=query, timeout=60)
            if not isinstance(body, dict):
                break
            page = body.get("roms") or []
            if fields:
                page = [{key: row.get(key) for key in fields} for row in page]
            roms.extend(page)
            if progress:
                progress(len(roms), body.get("total") or 0)
            offset += len(page)
            if not body.get("has_more") or not page:
                break
        return roms
