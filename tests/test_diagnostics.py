from green.services.diagnostics import build_report


def _items(report):
    return {item.label: item for item in report.items}


def test_report_with_running_ollama(services):
    report = build_report(services)
    items = _items(report)
    assert items["GREEN version"].value == "2.0.1"
    assert items["Ollama reachable"].ok is True
    assert items["Model installed"].ok is True
    assert items["Database"].ok is True
    assert "not probed" in items["GPU / VRAM"].value
    assert "GREEN diagnostics report" in report.as_text()


def test_report_with_ollama_down(services_factory, closed_port_url):
    services = services_factory(closed_port_url)
    items = _items(build_report(services))
    assert items["Ollama reachable"].ok is False
    assert "Model installed" not in items
    assert items["Voice"].value == "disabled in Settings"
