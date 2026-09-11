"""SEC ownership discovery includes current XML and historical form names."""

import pytest
from scutio_data import capital
from scutio_data._providers import sec


@pytest.mark.parametrize("page_size", [1, 50])
def test_ownership_filings_keeps_schedule_and_legacy_forms(monkeypatch, page_size):
    forms = [
        "SCHEDULE 13D",
        "SCHEDULE 13D/A",
        "SCHEDULE 13G",
        "SCHEDULE 13G/A",
        "SC 13D",
        "SC 13D/A",
        "SC 13G",
        "SC 13G/A",
        "4",
        "10-K",
    ]
    payload = {
        "filings": {
            "recent": {
                "form": forms,
                "accessionNumber": [f"0001731530-25-{i:06}" for i in range(len(forms))],
                "primaryDocument": ["primary_doc.xml"] * len(forms),
                "filingDate": ["2025-05-09"] * len(forms),
            }
        }
    }
    monkeypatch.setattr(sec, "load_submissions", lambda code: ("TEST", "0001475115", payload))
    result = capital.ownership_filings("usTEST", page_size=page_size)
    assert result["ok"]
    assert [row["form"] for row in result["items"]] == forms[:-1][:page_size]
    assert all(row["ticker"] == "TEST" and row["cik"] == "0001475115" for row in result["items"])
    assert result["items"][0]["file_url"] == (
        "https://www.sec.gov/Archives/edgar/data/1475115/000173153025000000/primary_doc.xml"
    )
