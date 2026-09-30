# Security policy

## Supported versions

aiodrf has not published a stable release yet. Once it has, security fixes are
released for the latest patch release of each supported version. The supported
Django, DRF and Python versions are listed in the [README](README.md#compatibility);
using an unsupported Django or DRF version with aiodrf does not make it supported.

## Reporting a vulnerability

Please do not report vulnerabilities in public issues. Use GitHub's
[private vulnerability reporting](https://github.com/django-aiodrf/django-aiodrf/security/advisories/new), or write to
[cevatbatuhan.tolon@gmail.com](mailto:cevatbatuhan.tolon@gmail.com). Include the
affected versions, a minimal reproduction and the security boundary you expect
to hold, but no production credentials or personal data. A disclosure date is
agreed once the report has been assessed.

## Secure use

When you adapt hooks, keep authentication, CSRF protection, object permissions
and transaction boundaries in place. Declare synchronous code safe for the event
loop (`async_safe`, `AIODRF["PURE_POLICIES"]`) only when it performs no I/O.
Experimental integrations document their additional limits in their guides.
