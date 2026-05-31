"""
Integration tests: health-check endpoints.
"""

import pytest


@pytest.mark.anyio
async def test_ping(client):
    response = await client.get("/ping")
    assert response.status_code == 200
    assert response.json() == {"message": "pong"}


@pytest.mark.anyio
async def test_root(client):
    response = await client.get("/")
    assert response.status_code == 200
    data = response.json()
    assert "message" in data
