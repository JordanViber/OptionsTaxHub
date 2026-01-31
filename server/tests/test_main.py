from fastapi.testclient import TestClient

from main import app


client = TestClient(app)


def test_health():
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_upload_csv_returns_first_five_rows():
    csv_content = """symbol,qty,price
AAPL,10,150
MSFT,5,310
TSLA,2,220
AMZN,1,130
GOOGL,3,140
NVDA,4,450
"""
    files = {"file": ("test.csv", csv_content, "text/csv")}
    response = client.post("/upload-csv", files=files)

    assert response.status_code == 200
    data = response.json()
    assert isinstance(data, list)
    assert len(data) == 5
    assert data[0] == {"symbol": "AAPL", "qty": 10, "price": 150}
    assert data[-1] == {"symbol": "GOOGL", "qty": 3, "price": 140}
