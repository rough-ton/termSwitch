"""Release signing rules: what may be signed, when, and that nothing unsigned or
unverified is published while signing is on. Read as text: CI has no YAML parser."""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RELEASE = (ROOT / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")


def job(name: str) -> str:
    """The text of one job, up to the next job."""
    m = re.search(rf"^  {re.escape(name)}:\n(.*?)(?=^  [a-z][\w-]*:\n|\Z)", RELEASE, re.M | re.S)
    assert m, f"job {name} not found"
    return m.group(1)


def test_every_action_is_pinned_to_a_commit():
    for path in (ROOT / ".github" / "workflows").glob("*.yml"):
        for line in path.read_text(encoding="utf-8").splitlines():
            m = re.match(r"\s*(?:-\s*)?uses:\s*(\S+)", line)
            if m and not m.group(1).startswith("./"):
                assert re.search(r"@[0-9a-f]{40}$", m.group(1)), f"{path.name}: {line.strip()}"


def test_signing_runs_only_for_releases_from_main_when_switched_on():
    sign = job("sign-windows")
    assert ("if: github.event_name == 'push' && github.ref == 'refs/heads/main' && "
            "vars.SIGNPATH_ORGANIZATION_ID != ''") in sign
    assert "environment: release" in sign
    assert "secrets.SIGNPATH_API_TOKEN" in sign
    # The token is only ever used by the signing job.
    assert RELEASE.count("secrets.SIGNPATH_API_TOKEN") == 1
    assert "signpath/github-action-submit-signing-request@" in sign
    # The build marks the Windows file for signing on the same conditions.
    assert ("matrix.os == 'windows-latest' && github.event_name == 'push' && "
            "github.ref == 'refs/heads/main' && vars.SIGNPATH_ORGANIZATION_ID != ''") in job("build")


def test_the_signing_jobs_run_on_github_hosted_runners():
    # SignPath's open source program requires it for every job before signing.
    for name in ("version", "build", "sign-windows", "verify-windows"):
        assert "self-hosted" not in job(name)
        assert "vars.LINUX_RUNNER" not in job(name)


def test_signature_is_verified_on_windows_before_publishing():
    verify = job("verify-windows")
    assert "runs-on: windows-latest" in verify
    for check in ("$sig.Status -ne 'Valid'", "'SignPath Foundation'",
                  "TimeStamperCertificate", "'--version'"):
        assert check in verify, check
    assert "name: termSwitch-windows-x64.exe" in verify


def test_publish_never_takes_an_unsigned_build_while_signing_is_on():
    publish = job("publish")
    assert "needs: [version, build, verify-windows]" in publish
    assert "needs.verify-windows.result == 'success' ||" in publish
    assert ("(vars.SIGNPATH_ORGANIZATION_ID == '' && "
            "needs.verify-windows.result == 'skipped')") in publish
    # Only final files: never the unsigned- or signed- staging artifacts.
    assert "pattern: termSwitch-*" in publish
    assert "format('unsigned-{0}', matrix.artifact)" in job("build")
    # Checksums come from the files that are published.
    assert publish.index("sha256sum termSwitch-*") < publish.index("gh release create")
    assert "Free code signing provided by SignPath.io, certificate by SignPath Foundation" \
        in publish


def test_artifact_configuration_signs_only_termswitch():
    xml = (ROOT / "signing" / "signpath-artifact-configuration.xml").read_text(encoding="utf-8")
    assert 'xmlns="http://signpath.io/artifact-configuration/v1"' in xml
    assert '<pe-file path="termSwitch-windows-x64.exe" max-matches="1"' in xml
    assert 'product-name="termSwitch"' in xml and "<authenticode-sign />" in xml
    spec = (ROOT / "termswitch.spec").read_text(encoding="utf-8")
    assert 'vi.StringStruct("ProductName", NAME)' in spec
    assert 'vi.StringStruct("OriginalFilename", f"{NAME}.exe")' in spec


def test_code_signing_policy_is_on_the_home_page():
    # SignPath Foundation requires this on the project's home page.
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert "## Code signing policy" in readme
    assert "Free code signing provided by [SignPath.io]" in readme
    assert "certificate by\n[SignPath Foundation]" in readme
    policy = (ROOT / "docs" / "code-signing-policy.md").read_text(encoding="utf-8")
    for section in ("## Team roles", "## Privacy policy", "## What is signed"):
        assert section in policy
    assert "Right-click" not in readme.split("macOS 15 (Sequoia) and later")[1].split("macOS 12")[0]
