from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from pydantic import BaseModel
from app.exceptions import add_exception_handlers

app = FastAPI()
add_exception_handlers(app)

class TestModel(BaseModel):
    name: str

@app.get("/error/500")
async def trigger_500():
    raise ValueError("This is a test 500 error")

@app.get("/error/404")
async def trigger_404():
    raise HTTPException(status_code=404, detail="Item not found")

@app.post("/error/422")
async def trigger_422(data: TestModel):
    return data

client = TestClient(app, raise_server_exceptions=False)

def test_500_handler():
    response = client.get("/error/500")
    assert response.status_code == 500
    assert response.json() == {
        "status": "error",
        "error_message": "Internal Server Error"
    }

def test_404_handler():
    response = client.get("/error/404")
    assert response.status_code == 404
    assert response.json() == {
        "status": "error",
        "error_message": "Item not found"
    }

def test_422_handler():
    response = client.post("/error/422", json={}) # Missing 'name' required locally
    assert response.status_code == 422
    data = response.json()
    assert data["status"] == "error"
    assert data["error_message"] == "Validation Error"
    assert "details" in data
