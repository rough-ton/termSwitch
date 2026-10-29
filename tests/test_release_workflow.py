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


SIGN_JOBS = ("sign-windows-app", "sign-windows-setup")


def test_signing_runs_only_for_releases_from_main_when_switched_on():
    for name in SIGN_JOBS:
        sign = job(name)
        assert ("if: github.event_name == 'push' && github.ref == 'refs/heads/main' && "
                "vars.SIGNPATH_ORGANIZATION_ID != ''") in sign, name
        assert "environment: release" in sign, name
        assert sign.count("secrets.SIGNPATH_API_TOKEN") == 1, name
        assert "signpath/github-action-submit-signing-request@" in sign, name
    # The token is only ever used by the two signing jobs.
    assert RELEASE.count("secrets.SIGNPATH_API_TOKEN") == 2
    assert "artifact-configuration-slug: windows-app" in job("sign-windows-app")
    assert "artifact-configuration-slug: windows-setup" in job("sign-windows-setup")
    # The build marks the Windows build for signing on the same conditions.
    assert ("matrix.os == 'windows-latest' && github.event_name == 'push' && "
            "github.ref == 'refs/heads/main' && vars.SIGNPATH_ORGANIZATION_ID != ''") in job("build")


def test_signing_happens_in_order():
    # exe signed -> installer built around it -> installer signed -> verified -> published
    assert "needs: [version, build]" in job("sign-windows-app")
    assert "needs: [version, sign-windows-app]" in job("package-windows")
    assert "needs: [version, package-windows]" in job("sign-windows-setup")
    assert "needs: [version, package-windows, sign-windows-setup]" in job("verify-windows")
    package = job("package-windows")
    assert package.index("check_signature.ps1 signed/termSwitch.exe") \
        < package.index("Copy-Item signed/termSwitch.exe dist/termSwitch/termSwitch.exe") \
        < package.index("build_installer.ps1")


def test_the_signing_jobs_run_on_github_hosted_runners():
    # SignPath's open source program requires it for every job before signing.
    for name in ("version", "build", *SIGN_JOBS, "package-windows", "verify-windows"):
        assert "self-hosted" not in job(name)
        assert "vars.LINUX_RUNNER" not in job(name)


def test_signatures_are_verified_on_windows_before_publishing():
    verify = job("verify-windows")
    assert "runs-on: windows-latest" in verify
    assert "check_signature.ps1 dist/termSwitch-windows-x64-setup.exe" in verify
    assert 'check_signature.ps1 "$env:RUNNER_TEMP/portable/termSwitch/termSwitch.exe"' in verify
    assert "-RequireSignature" in verify       # the installed exe, too
    assert "name: termSwitch-windows-x64\n" in verify
    check = (ROOT / "packaging" / "windows" / "check_signature.ps1").read_text(encoding="utf-8")
    for rule in ("$sig.Status -ne 'Valid'", "'SignPath Foundation'", "TimeStamperCertificate"):
        assert rule in check, rule


def test_publish_never_takes_an_unsigned_build_while_signing_is_on():
    publish = job("publish")
    assert "needs: [version, build, verify-windows]" in publish
    assert "needs.verify-windows.result == 'success' ||" in publish
    assert ("(vars.SIGNPATH_ORGANIZATION_ID == '' && "
            "needs.verify-windows.result == 'skipped')") in publish
    # Only final files: never the staging artifacts, whose names don't start with termSwitch-.
    assert "pattern: termSwitch-*" in publish
    staging = re.findall(r"^\s+name: ([\w-]+)$", RELEASE, re.M)
    for name in ("unsigned-windows-exe", "windows-app-folder", "signed-windows-exe",
                 "unsigned-windows-setup", "windows-portable", "signed-windows-setup"):
        assert name in staging and not name.startswith("termSwitch-"), name
    build = job("build")
    assert "if: env.SIGN_WINDOWS != 'true'" in build    # final names only when unsigned
    # Checksums come from the files that are published.
    assert publish.index("sha256sum termSwitch-*") < publish.index("gh release create")
    assert "Free code signing provided by SignPath.io, certificate by SignPath Foundation" \
        in publish


def test_artifact_configurations_sign_only_termswitch():
    for slug, file, original in (
            ("windows-app", "termSwitch.exe", "termSwitch.exe"),
            ("windows-setup", "termSwitch-windows-x64-setup.exe",
             "termSwitch-windows-x64-setup.exe")):
        xml = (ROOT / "signing" / f"signpath-{slug}.xml").read_text(encoding="utf-8")
        assert 'xmlns="http://signpath.io/artifact-configuration/v1"' in xml
        assert f'<pe-file path="{file}" max-matches="1"' in xml
        assert f'product-name="termSwitch" original-filename="{original}"' in xml
        assert "<authenticode-sign />" in xml and xml.count("<pe-file") == 1
    spec = (ROOT / "termswitch.spec").read_text(encoding="utf-8")
    assert 'vi.StringStruct("ProductName", NAME)' in spec
    assert 'vi.StringStruct("OriginalFilename", f"{NAME}.exe")' in spec
    iss = (ROOT / "packaging" / "windows" / "termswitch.iss").read_text(encoding="utf-8")
    assert "VersionInfoProductName={#AppName}" in iss
    assert '#define SetupName "termSwitch-windows-x64-setup"' in iss
    assert "VersionInfoOriginalFileName={#SetupName}.exe" in iss


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
