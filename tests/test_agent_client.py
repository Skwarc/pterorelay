import hashlib
import hmac
import unittest

import aiohttp

from agent_client import AgentClient, DiscordActor


class AgentClientTests(unittest.TestCase):
    def test_signature_matches_documented_wire_format(self):
        client = AgentClient("https://panel.example.com", "agent-id", "secret")
        headers = client.signed_headers("POST", "/pterosync-agent/heartbeat", b"{}", timestamp=123, nonce="abc")
        message = f"POST\n/pterosync-agent/heartbeat\n123\nabc\n{hashlib.sha256(b'{}').hexdigest()}".encode()
        self.assertEqual(headers["X-PteroSync-Signature"], hmac.new(b"secret", message, hashlib.sha256).hexdigest())

    def test_actor_serialization_uses_string_ids(self):
        actor = DiscordActor("1", "2", ("3", "4"))
        self.assertEqual(actor.payload()["role_ids"], ["3", "4"])


class FakeResponse:
    status = 200

    def raise_for_status(self):
        pass

    async def json(self):
        return {"ok": True}


class FakeSession:
    """Fails the first ``failures`` requests with a reset connection."""

    def __init__(self, failures):
        self.failures = failures
        self.nonces = []

    def request(self, method, url, data=None, headers=None):
        session = self

        class Context:
            async def __aenter__(self):
                session.nonces.append(headers["X-PteroSync-Nonce"])
                if len(session.nonces) <= session.failures:
                    raise aiohttp.ClientOSError(104, "Connection reset by peer")
                return FakeResponse()

            async def __aexit__(self, *exc):
                return False

        return Context()


class AgentClientRetryTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.client = AgentClient("https://panel.example.com", "agent-id", "secret")

    async def test_heartbeat_and_reads_retry_once_with_a_fresh_nonce(self):
        session = FakeSession(failures=1)
        self.assertEqual(await self.client.heartbeat(session, []), {"ok": True})
        self.assertEqual(len(session.nonces), 2)
        self.assertNotEqual(session.nonces[0], session.nonces[1])
        session = FakeSession(failures=1)
        self.assertEqual(await self.client.config(session), {"ok": True})

    async def test_actions_that_must_not_repeat_are_not_retried(self):
        actor = DiscordActor("1", "2", ("3",))
        for call in (
            lambda session: self.client.chat(session, "uuid", ["say hi"], actor),
            lambda session: self.client.power(session, "uuid", "restart", actor),
            lambda session: self.client.command(session, "uuid", "op Bob", actor),
        ):
            session = FakeSession(failures=1)
            with self.assertRaises(aiohttp.ClientOSError):
                await call(session)
            self.assertEqual(len(session.nonces), 1)

    async def test_persistent_failure_is_raised_after_one_retry(self):
        session = FakeSession(failures=5)
        with self.assertRaises(aiohttp.ClientOSError):
            await self.client.heartbeat(session, [])
        self.assertEqual(len(session.nonces), 2)
