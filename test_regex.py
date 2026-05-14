from shellstory.redact import regex_redact

text = '$env:AWS_ACCESS_KEY_ID="AKIAIOSFODNN7EXAMPLE"'
redacted, findings = regex_redact(text)
print("Findings:", findings)
print("Redacted:", redacted)
