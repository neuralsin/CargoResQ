"""
Shared test fixtures.

The test database is a throwaway file, configured before any application
module is imported. The previous fixture ran ``Base.metadata.drop_all``
against the *live* engine, so running the suite destroyed the working
``cargoresq.db`` in the repository root, and every test file except one
depended on that fixture having run first to create its tables at all.
"""
import itertools
import os
import tempfile
import uuid
from dataclasses import dataclass
from pathlib import Path

import pytest
import pytest_asyncio

# Point the application at a scratch database *before* importing anything that
# reads settings. shared.database builds its engine at import time.
_TEST_DB = Path(tempfile.gettempdir()) / f"cargoresq_test_{uuid.uuid4().hex[:8]}.db"
os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{_TEST_DB.as_posix()}"
os.environ.setdefault("JWT_SECRET", "test-only-secret-not-used-in-any-deployment")
os.environ["ENVIRONMENT"] = "test"

from httpx import ASGITransport, AsyncClient  # noqa: E402

from main import app  # noqa: E402
from shared.database import engine  # noqa: E402
from shared.schema import ensure_schema  # noqa: E402
import shared.models_registry  # noqa: F401,E402


@dataclass
class Account:
    """A registered company plus the headers needed to act as it."""

    company_id: str
    email: str
    password: str
    token: str

    @property
    def headers(self) -> dict:
        return {"Authorization": f"Bearer {self.token}"}


@pytest.fixture(scope="session", autouse=True)
def _create_schema():
    """Build the schema once for the whole session, then remove the file."""
    import asyncio

    asyncio.run(ensure_schema())
    yield
    asyncio.run(engine.dispose())
    try:
        _TEST_DB.unlink(missing_ok=True)
    except OSError:
        # Windows may still hold the handle briefly; a stray temp file is
        # not worth failing a test run over.
        pass


@pytest_asyncio.fixture
async def client():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


_LOCATION_SEQ = itertools.count()


@pytest.fixture
def region():
    """A patch of map used by exactly one test.

    Matching is deliberately cross-carrier: it finds every compatible idle
    truck near a breakdown, regardless of who owns it. That is the product,
    but it means tests sharing a database also share a map, and one test's
    leftover reefer becomes another test's surprise candidate. Each test gets
    coordinates far enough from the others that no search radius reaches them.
    """
    index = next(_LOCATION_SEQ)
    # 2 degrees apart is ~220 km, well beyond any radius used in tests.
    return {"lat": -60.0 + (index % 40) * 2.0, "lng": -170.0 + (index // 40) * 2.0}


@pytest.fixture
def new_company(client: AsyncClient):
    """Factory fixture: await new_company("Name") -> Account.

    Exposed as a fixture rather than an importable helper because an unrelated
    `tests` package in site-packages shadows this one on import.
    """

    async def _factory(name: str) -> Account:
        return await register_company(client, name)

    return _factory


@pytest.fixture
def new_driver(client: AsyncClient):
    """Factory fixture: await new_driver(owner_account, "Name") -> dict."""

    async def _factory(owner: Account, name: str) -> dict:
        return await register_driver(client, owner, name)

    return _factory


async def register_company(client: AsyncClient, name: str) -> Account:
    """Register a fresh company and return credentials for it.

    Each call uses a unique email so tests never collide, which means they do
    not need a shared database reset and can run in any order.
    """
    email = f"{name.lower().replace(' ', '-')}-{uuid.uuid4().hex[:8]}@example.com"
    password = "TestPassword123!"
    res = await client.post(
        "/api/v1/auth/register",
        json={"name": name, "email": email, "password": password},
    )
    assert res.status_code == 201, res.text
    body = res.json()
    return Account(
        company_id=body["company_id"],
        email=email,
        password=password,
        token=body["access_token"],
    )


async def register_driver(client: AsyncClient, owner: Account, name: str) -> dict:
    """Onboard a driver under an existing company."""
    email = f"{name.lower().replace(' ', '-')}-{uuid.uuid4().hex[:8]}@example.com"
    password = "DriverPassword123!"
    res = await client.post(
        "/api/v1/driver/register",
        json={"name": name, "email": email, "password": password},
        headers=owner.headers,
    )
    assert res.status_code == 201, res.text
    body = res.json()
    body["email"] = email
    body["password"] = password
    return body
