"""Wires the platform's backing services together for the reconcilers.

It resolves an authenticated OpenBao client (the source of truth for credentials)
and, from the bootstrap token OpenBao holds, an Authentik client. The defaults
drop the operator into the kubernetes-provisioner setup.
"""

from __future__ import annotations

import base64
import threading
import time

from kubernetes import client
from kubernetes.client.exceptions import ApiException

from .authentik import AuthentikClient
from .config import AuthMode, Config
from .openbao import OpenBaoClient, kubernetes_login

# How long before a leased OpenBao token expires the provider proactively logs in
# again, so a reconcile never uses a token about to expire.
_TOKEN_RENEW_SKEW = 60  # seconds


class Provider:
    """Builds authenticated backend clients on demand.

    The kubernetes-auth token is cached behind a lock so concurrent reconciles
    share a single login per lease instead of logging in on every call.
    """

    def __init__(self, cfg: Config) -> None:
        self._cfg = cfg
        self._lock = threading.Lock()
        self._cached_token = ""
        self._token_expiry = 0.0  # monotonic deadline; 0 means non-expiring

    @property
    def config(self) -> Config:
        return self._cfg

    def openbao(self) -> OpenBaoClient:
        """Return an OpenBao client wired to this provider's token source."""
        return OpenBaoClient(self._cfg.openbao_addr, self._cfg.kv_mount, self._openbao_token)

    def _openbao_token(self, force_refresh: bool) -> str:
        """The token source backing every OpenBao client this provider hands out."""
        if self._cfg.auth_mode == AuthMode.KUBERNETES:
            return self._kubernetes_token(force_refresh)
        return self._token_from_secret()

    def _token_from_secret(self) -> str:
        cfg = self._cfg
        try:
            secret = client.CoreV1Api().read_namespaced_secret(
                cfg.token_secret_name, cfg.token_secret_namespace
            )
        except ApiException as exc:
            raise RuntimeError(
                f"read OpenBao token secret {cfg.token_secret_namespace}/{cfg.token_secret_name}: {exc.reason}"
            ) from exc

        raw = (secret.data or {}).get(cfg.token_secret_key)
        token = base64.b64decode(raw).decode().strip() if raw else ""
        if not token:
            raise RuntimeError(
                f"OpenBao token secret {cfg.token_secret_namespace}/{cfg.token_secret_name} "
                f"has no {cfg.token_secret_key!r}"
            )
        return token

    def _kubernetes_token(self, force_refresh: bool) -> str:
        """Return a valid OpenBao token via kubernetes auth, logging in only as needed."""
        cfg = self._cfg
        with self._lock:
            fresh_enough = self._token_expiry == 0 or time.monotonic() < (
                self._token_expiry - _TOKEN_RENEW_SKEW
            )
            if not force_refresh and self._cached_token and fresh_enough:
                return self._cached_token

            with open(cfg.jwt_path, encoding="utf-8") as fh:
                jwt = fh.read().strip()

            token, lease = kubernetes_login(
                cfg.openbao_addr, cfg.k8s_auth_mount, cfg.k8s_auth_role, jwt
            )

            self._cached_token = token
            self._token_expiry = time.monotonic() + lease if lease > 0 else 0.0
            return token

    def authentik(self, bao: OpenBaoClient) -> AuthentikClient:
        """Return an authenticated Authentik client.

        Reads the API token from OpenBao at the configured path/key and verifies it
        before returning, so callers get a clear backend-not-ready error.
        """
        cfg = self._cfg
        data = bao.read(cfg.authentik_secret_path)
        if data is None:
            raise RuntimeError(
                f"authentik secret {cfg.authentik_secret_path!r} not present in OpenBao yet"
            )

        token = (data.get(cfg.authentik_token_key) or "").strip()
        if not token:
            raise RuntimeError(
                f"authentik token key {cfg.authentik_token_key!r} missing at "
                f"OpenBao path {cfg.authentik_secret_path!r}"
            )

        ak = AuthentikClient(cfg.authentik_addr, token)
        try:
            ak.verify()
        except Exception as exc:  # noqa: BLE001 - wrap as not-ready
            raise RuntimeError(f"authentik API not ready: {exc}") from exc
        return ak