import pytest
import json
from aiohttp.test_utils import TestClient, TestServer
from services.web.server import create_web_app

@pytest.mark.asyncio
async def test_web_health():
    app = create_web_app()
    async with TestClient(TestServer(app)) as client:
        resp = await client.get("/api/health")
        assert resp.status == 200
        data = await resp.json()
        assert data["status"] == "healthy"
        assert "PilotBrief" in data["service"]

@pytest.mark.asyncio
async def test_web_config():
    app = create_web_app()
    async with TestClient(TestServer(app)) as client:
        resp = await client.get("/api/config")
        assert resp.status == 200
        data = await resp.json()
        assert "home_icao" in data

@pytest.mark.asyncio
async def test_web_insights():
    app = create_web_app()
    async with TestClient(TestServer(app)) as client:
        resp = await client.get("/api/system/insights")
        assert resp.status == 200
        data = await resp.json()
        assert "server" in data
        assert "memory" in data
        assert "requests" in data
        assert "caches" in data
        assert "lightning" in data

@pytest.mark.asyncio
async def test_web_airport_search():
    app = create_web_app()
    async with TestClient(TestServer(app)) as client:
        resp = await client.get("/api/airports/search?q=KPAO")
        assert resp.status == 200
        data = await resp.json()
        assert "results" in data
        assert len(data["results"]) >= 1
        assert data["results"][0]["icao"] == "KPAO"

@pytest.mark.asyncio
async def test_web_route():
    app = create_web_app()
    async with TestClient(TestServer(app)) as client:
        resp = await client.get("/api/route?dep=KPAO&dest=KMRY")
        assert resp.status == 200
        data = await resp.json()
        assert data["dep"]["icao"] == "KPAO"
        assert data["dest"]["icao"] == "KMRY"
        assert "route" in data
        assert data["route"]["distance_nm"] > 0
        assert "range_rings" in data
        assert len(data["range_rings"]) == 4

@pytest.mark.asyncio
async def test_web_radar_frames():
    app = create_web_app()
    async with TestClient(TestServer(app)) as client:
        resp = await client.get("/api/weather/radar-frames")
        assert resp.status == 200
        data = await resp.json()
        assert "frames" in data
        assert len(data["frames"]) > 0
        assert "tile_url" in data["frames"][0] or "wms_url" in data["frames"][0]

@pytest.mark.asyncio
async def test_web_lightning():
    app = create_web_app()
    async with TestClient(TestServer(app)) as client:
        resp = await client.get("/api/weather/lightning")
        assert resp.status == 200
        data = await resp.json()
        assert "strikes" in data
        assert "stats" in data

@pytest.mark.asyncio
async def test_web_index():
    app = create_web_app()
    async with TestClient(TestServer(app)) as client:
        resp = await client.get("/")
        assert resp.status == 200
        text = await resp.text()
        assert "PilotBrief" in text
