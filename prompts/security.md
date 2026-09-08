# Security reasoning task

You receive deterministic security EVIDENCE (secret scan, SAST, dep audit). You may ADD context findings but MUST NOT downgrade or dismiss any deterministic CRITICAL/HIGH.
Flag: hardcoded secrets, injection (SQL/XSS/command), SSRF, authZ/authN gaps, insecure deser, unsafe fs/exec, exposed creds, missing controls.
Security findings default auto_fixable=false unless the fix is a rote safe change (e.g. parameterized query with test coverage) AND confidence>=0.9.
