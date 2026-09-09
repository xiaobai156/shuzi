import ast
import inspect
from pathlib import Path

import crawler
from kill_numbers.application import crawl_service


ROOT = Path(__file__).resolve().parent
ENTRY_MODULES = {"crawler", "check_duplicates", "validate_failed_sites"}


def imported_entry_modules(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    found = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.update(alias.name.split(".", 1)[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            found.add(node.module.split(".", 1)[0])
    return found & ENTRY_MODULES


def test_formal_single_target_pipeline_lives_in_application_not_legacy_entry():
    entry_source = inspect.getsource(crawler.crawl_one)
    service_source = inspect.getsource(crawl_service._run_formal_crawl_target)
    assert "target_policy" in inspect.getsource(crawl_service.run_formal_crawl_target)

    assert "run_formal_crawl_target" in entry_source
    assert "parse_target_content" not in entry_source
    assert "validate_issue_map" in service_source
    assert not hasattr(crawler, "_crawl_one_impl")


def test_lower_layers_do_not_import_command_line_entry_modules():
    package = ROOT / "kill_numbers"
    violations = {
        path.relative_to(ROOT).as_posix(): imported_entry_modules(path)
        for path in package.rglob("*.py")
        if imported_entry_modules(path)
    }

    assert violations == {}


def test_all_three_command_line_entries_use_application_services():
    sources = {
        path.name: path.read_text(encoding="utf-8")
        for path in (
            ROOT / "crawler.py",
            ROOT / "check_duplicates.py",
            ROOT / "validate_failed_sites.py",
        )
    }

    assert "kill_numbers.application" in sources["crawler.py"]
    assert "kill_numbers.application" in sources["check_duplicates.py"]
    assert "kill_numbers.application" in sources["validate_failed_sites.py"]
