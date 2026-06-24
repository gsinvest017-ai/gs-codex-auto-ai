"""Tests for src/codexautoai_v2/secret_scan.py — REVIEW-R4 / SECGOV-R3."""
import pytest

from src.codexautoai_v2.secret_scan import (
    Finding,
    Severity,
    blocks_delivery,
    scan,
    scan_sast,
    scan_secrets,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _has_rule(findings: list[Finding], rule: str) -> bool:
    return any(f.rule == rule for f in findings)


def _findings_for_rule(findings: list[Finding], rule: str) -> list[Finding]:
    return [f for f in findings if f.rule == rule]


# ---------------------------------------------------------------------------
# Severity constants
# ---------------------------------------------------------------------------

class TestSeverityConstants:
    def test_low(self):
        assert Severity.LOW == "low"

    def test_medium(self):
        assert Severity.MEDIUM == "medium"

    def test_high(self):
        assert Severity.HIGH == "high"


# ---------------------------------------------------------------------------
# Finding dataclass
# ---------------------------------------------------------------------------

class TestFindingDataclass:
    def test_fields(self):
        f = Finding(rule="X", severity=Severity.HIGH, line=1, message="msg")
        assert f.rule == "X"
        assert f.severity == Severity.HIGH
        assert f.line == 1
        assert f.message == "msg"

    def test_equality(self):
        f1 = Finding(rule="X", severity=Severity.HIGH, line=1, message="msg")
        f2 = Finding(rule="X", severity=Severity.HIGH, line=1, message="msg")
        assert f1 == f2


# ---------------------------------------------------------------------------
# scan_secrets — OpenAI key
# ---------------------------------------------------------------------------

class TestScanSecretsOpenAI:
    def test_detects_openai_key(self):
        src = 'api_key = "sk-abcdefghijklmnopqrstuvwxyz1234"'
        findings = scan_secrets(src)
        # At minimum OPENAI_API_KEY should fire; HARDCODED_CREDENTIAL may also fire
        assert _has_rule(findings, "OPENAI_API_KEY")

    def test_openai_key_is_high(self):
        src = 'key = "sk-abcdefghijklmnopqrstuvwxyz1234"'
        findings = scan_secrets(src)
        hi = [f for f in findings if f.rule == "OPENAI_API_KEY"]
        assert hi, "No OPENAI_API_KEY finding"
        assert hi[0].severity == Severity.HIGH

    def test_openai_key_blocks_delivery(self):
        src = 'key = "sk-abcdefghijklmnopqrstuvwxyz1234"'
        assert blocks_delivery(scan_secrets(src)) is True

    def test_short_sk_not_flagged(self):
        # sk- with fewer than 20 alphanumeric chars should not match
        src = 'x = "sk-short"'
        findings = scan_secrets(src)
        assert not _has_rule(findings, "OPENAI_API_KEY")


# ---------------------------------------------------------------------------
# scan_secrets — AWS key
# ---------------------------------------------------------------------------

class TestScanSecretsAWS:
    def test_detects_aws_key(self):
        src = 'aws_key = "AKIAIOSFODNN7EXAMPLE"'
        findings = scan_secrets(src)
        assert _has_rule(findings, "AWS_ACCESS_KEY")

    def test_aws_key_is_high(self):
        src = 'key = "AKIAIOSFODNN7EXAMPLE"'
        findings = scan_secrets(src)
        hi = [f for f in findings if f.rule == "AWS_ACCESS_KEY"]
        assert hi
        assert hi[0].severity == Severity.HIGH

    def test_aws_key_blocks_delivery(self):
        src = 'key = "AKIAIOSFODNN7EXAMPLE"'
        assert blocks_delivery(scan_secrets(src)) is True

    def test_akia_wrong_length_not_flagged(self):
        # AKIA + 15 chars (one short) should not match
        src = 'x = "AKIASHORT123456"'  # AKIA + 12 chars
        findings = scan_secrets(src)
        assert not _has_rule(findings, "AWS_ACCESS_KEY")


# ---------------------------------------------------------------------------
# scan_secrets — hardcoded credential assignment
# ---------------------------------------------------------------------------

class TestScanSecretsCredential:
    @pytest.mark.parametrize("varname", ["password", "passwd", "secret", "api_key"])
    def test_detects_credential_assignment(self, varname: str):
        src = f'{varname} = "supersecret123"'
        findings = scan_secrets(src)
        assert _has_rule(findings, "HARDCODED_CREDENTIAL")

    def test_credential_is_high(self):
        src = 'password = "hunter2"'
        findings = scan_secrets(src)
        hi = [f for f in findings if f.rule == "HARDCODED_CREDENTIAL"]
        assert hi
        assert hi[0].severity == Severity.HIGH

    def test_case_insensitive(self):
        src = 'PASSWORD = "hunter2"'
        findings = scan_secrets(src)
        assert _has_rule(findings, "HARDCODED_CREDENTIAL")


# ---------------------------------------------------------------------------
# scan_secrets — Bearer token (MEDIUM, does NOT block)
# ---------------------------------------------------------------------------

class TestScanSecretsBearerToken:
    def test_detects_bearer(self):
        src = 'headers = {"Authorization": "Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9"}'
        findings = scan_secrets(src)
        assert _has_rule(findings, "BEARER_TOKEN")

    def test_bearer_is_medium(self):
        src = 'auth = "Bearer mytoken123"'
        findings = scan_secrets(src)
        med = [f for f in findings if f.rule == "BEARER_TOKEN"]
        assert med
        assert med[0].severity == Severity.MEDIUM

    def test_bearer_does_not_block_delivery(self):
        src = 'auth = "Bearer mytoken123"'
        findings = scan_secrets(src)
        # Remove any HIGH findings that might come from other rules
        medium_only = [f for f in findings if f.rule == "BEARER_TOKEN"]
        assert blocks_delivery(medium_only) is False


# ---------------------------------------------------------------------------
# scan_sast — eval / exec
# ---------------------------------------------------------------------------

class TestScanSastEvalExec:
    def test_detects_eval(self):
        src = "result = eval(user_input)"
        findings = scan_sast(src)
        assert _has_rule(findings, "EVAL_USAGE")

    def test_eval_is_high(self):
        src = "eval('x')"
        findings = scan_sast(src)
        hi = [f for f in findings if f.rule == "EVAL_USAGE"]
        assert hi
        assert hi[0].severity == Severity.HIGH

    def test_eval_blocks_delivery(self):
        src = "eval('x')"
        assert blocks_delivery(scan_sast(src)) is True

    def test_detects_exec(self):
        src = "exec(code)"
        findings = scan_sast(src)
        assert _has_rule(findings, "EXEC_USAGE")

    def test_exec_is_high(self):
        src = "exec(code)"
        findings = scan_sast(src)
        hi = [f for f in findings if f.rule == "EXEC_USAGE"]
        assert hi
        assert hi[0].severity == Severity.HIGH

    def test_exec_blocks_delivery(self):
        src = "exec(code)"
        assert blocks_delivery(scan_sast(src)) is True


# ---------------------------------------------------------------------------
# scan_sast — subprocess shell=True
# ---------------------------------------------------------------------------

class TestScanSastSubprocess:
    def test_detects_shell_true(self):
        src = "subprocess.run(cmd, shell=True)"
        findings = scan_sast(src)
        assert _has_rule(findings, "SUBPROCESS_SHELL_TRUE")

    def test_subprocess_shell_true_is_high(self):
        src = "subprocess.run(cmd, shell=True)"
        findings = scan_sast(src)
        hi = [f for f in findings if f.rule == "SUBPROCESS_SHELL_TRUE"]
        assert hi
        assert hi[0].severity == Severity.HIGH

    def test_subprocess_shell_true_blocks(self):
        src = "subprocess.run(cmd, shell=True)"
        assert blocks_delivery(scan_sast(src)) is True

    def test_subprocess_shell_false_not_flagged(self):
        src = "subprocess.run(cmd, shell=False)"
        findings = scan_sast(src)
        assert not _has_rule(findings, "SUBPROCESS_SHELL_TRUE")


# ---------------------------------------------------------------------------
# scan_sast — pickle.loads (MEDIUM)
# ---------------------------------------------------------------------------

class TestScanSastPickle:
    def test_detects_pickle_loads(self):
        src = "obj = pickle.loads(data)"
        findings = scan_sast(src)
        assert _has_rule(findings, "PICKLE_LOADS")

    def test_pickle_is_medium(self):
        src = "obj = pickle.loads(data)"
        findings = scan_sast(src)
        med = [f for f in findings if f.rule == "PICKLE_LOADS"]
        assert med
        assert med[0].severity == Severity.MEDIUM


# ---------------------------------------------------------------------------
# scan_sast — verify=False (MEDIUM)
# ---------------------------------------------------------------------------

class TestScanSastVerifyFalse:
    def test_detects_verify_false(self):
        src = 'requests.get(url, verify=False)'
        findings = scan_sast(src)
        assert _has_rule(findings, "REQUESTS_VERIFY_FALSE")

    def test_verify_false_is_medium(self):
        src = 'requests.get(url, verify=False)'
        findings = scan_sast(src)
        med = [f for f in findings if f.rule == "REQUESTS_VERIFY_FALSE"]
        assert med
        assert med[0].severity == Severity.MEDIUM


# ---------------------------------------------------------------------------
# scan_sast — yaml.load (MEDIUM)
# ---------------------------------------------------------------------------

class TestScanSastYamlLoad:
    def test_detects_yaml_load(self):
        src = "data = yaml.load(stream)"
        findings = scan_sast(src)
        assert _has_rule(findings, "YAML_LOAD_UNSAFE")

    def test_yaml_load_is_medium(self):
        src = "data = yaml.load(stream)"
        findings = scan_sast(src)
        med = [f for f in findings if f.rule == "YAML_LOAD_UNSAFE"]
        assert med
        assert med[0].severity == Severity.MEDIUM

    def test_yaml_safe_load_not_flagged(self):
        src = "data = yaml.safe_load(stream)"
        findings = scan_sast(src)
        assert not _has_rule(findings, "YAML_LOAD_UNSAFE")


# ---------------------------------------------------------------------------
# Clean code
# ---------------------------------------------------------------------------

class TestCleanCode:
    def test_clean_scan_secrets(self):
        src = "x = 1\nprint(x)\n"
        assert scan_secrets(src) == []

    def test_clean_scan_sast(self):
        src = "import os\nos.getcwd()\n"
        assert scan_sast(src) == []

    def test_clean_scan(self):
        src = "def add(a, b):\n    return a + b\n"
        assert scan(src) == []

    def test_clean_does_not_block(self):
        src = "def add(a, b):\n    return a + b\n"
        assert blocks_delivery(scan(src)) is False


# ---------------------------------------------------------------------------
# Combined scan()
# ---------------------------------------------------------------------------

class TestCombinedScan:
    def test_scan_combines_results(self):
        src = 'api_key = "sk-abcdefghijklmnopqrstuvwxyz1234"\nresult = eval(x)\n'
        findings = scan(src)
        rules = {f.rule for f in findings}
        assert "OPENAI_API_KEY" in rules
        assert "EVAL_USAGE" in rules

    def test_scan_blocks_on_high(self):
        src = 'key = "sk-abcdefghijklmnopqrstuvwxyz1234"'
        assert blocks_delivery(scan(src)) is True


# ---------------------------------------------------------------------------
# Line numbers
# ---------------------------------------------------------------------------

class TestLineNumbers:
    def test_line_number_correct_line3(self):
        src = "line1 = 1\nline2 = 2\nkey = 'sk-abcdefghijklmnopqrstuvwxyz1234'\n"
        findings = scan_secrets(src)
        openai_findings = [f for f in findings if f.rule == "OPENAI_API_KEY"]
        assert openai_findings, "No OPENAI_API_KEY finding"
        assert openai_findings[0].line == 3

    def test_line_number_first_line(self):
        src = "eval(x)\n"
        findings = scan_sast(src)
        eval_findings = [f for f in findings if f.rule == "EVAL_USAGE"]
        assert eval_findings
        assert eval_findings[0].line == 1

    def test_line_number_multiline_aws(self):
        src = "\n\nkey = 'AKIAIOSFODNN7EXAMPLE'\n"
        findings = scan_secrets(src)
        aws = [f for f in findings if f.rule == "AWS_ACCESS_KEY"]
        assert aws
        assert aws[0].line == 3

    def test_line_number_sast_line3(self):
        src = "x = 1\ny = 2\nresult = eval(user_input)\n"
        findings = scan_sast(src)
        eval_f = [f for f in findings if f.rule == "EVAL_USAGE"]
        assert eval_f
        assert eval_f[0].line == 3


# ---------------------------------------------------------------------------
# blocks_delivery
# ---------------------------------------------------------------------------

class TestBlocksDelivery:
    def test_empty_findings_does_not_block(self):
        assert blocks_delivery([]) is False

    def test_medium_only_does_not_block(self):
        findings = [Finding(rule="X", severity=Severity.MEDIUM, line=1, message="m")]
        assert blocks_delivery(findings) is False

    def test_low_only_does_not_block(self):
        findings = [Finding(rule="X", severity=Severity.LOW, line=1, message="m")]
        assert blocks_delivery(findings) is False

    def test_high_blocks(self):
        findings = [Finding(rule="X", severity=Severity.HIGH, line=1, message="m")]
        assert blocks_delivery(findings) is True

    def test_mixed_blocks_if_any_high(self):
        findings = [
            Finding(rule="A", severity=Severity.MEDIUM, line=1, message="m"),
            Finding(rule="B", severity=Severity.HIGH, line=2, message="h"),
        ]
        assert blocks_delivery(findings) is True
