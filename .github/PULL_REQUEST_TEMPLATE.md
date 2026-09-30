## Summary

Describe the behavior change, application requirement and related issue.

## Scope

- [ ] Bug fix
- [ ] Feature or integration
- [ ] Internal refactor or performance change
- [ ] Documentation/examples only
- [ ] Dependencies, tooling or workflows

## Compatibility and design

Explain affected Django/DRF versions, sync and async callers, override precedence,
default/opt-in paths and third-party integrations. Identify intentional response,
validation, authentication or query changes. Explain why existing Django/DRF
extension points are sufficient, or document a necessary exception.

- [ ] No implicit process-wide mutation or request state in shared globals.
- [ ] Any runtime adaptation is justified and recorded in the adaptation inventory.
- [ ] Cancellation, thread affinity, resource cleanup and concurrent use are covered.
- [ ] Security-sensitive behavior and data exposure have been reviewed.

## Tests and regression coverage

For a feature or bug fix, identify the focused test that failed before implementation
and passes afterward. Include error/default paths and relevant DRF reference behavior.
Explain any case where a red/green test does not apply.
Follow the [TDD workflow](../CONTRIBUTING.md#test-driven-development); report
observed failures and passes, not only the final test count.

Commands actually run and results:

```text
# Command, result and relevant environment.
```

- [ ] TDD regression covers the new behavior or reproduced defect.
- [ ] Relevant unit/integration and supported-version tests pass.
- [ ] Ruff, typing and warnings-as-errors checks pass.
- [ ] Service-dependent checks not run are explicitly listed.

## Documentation and release notes

- [ ] API/settings reference and runnable examples match the implementation.
- [ ] Regenerated llms.txt if public document titles or paths changed.
- [ ] Strict documentation build passes (`nox -s docs`); new site attachments are explicit.
- [ ] User-visible changes are recorded under Unreleased in CHANGELOG.md.
- [ ] Performance claims identify the workload and actual measurement evidence.

For documentation-only changes, explain why runtime/dependency behavior is unchanged
and list link, example or generated-document checks performed. Do not mark unrun
runtime tests as passed.

Changes confined to `examples/` run formatting and documentation checks, not tests.
Run the affected example locally and include its result above. Mixed framework or
release-input changes retain the normal branch test requirements.

## AI-assisted work

Follow [AI_POLICY.md](../AI_POLICY.md). If assistance materially contributed, identify
the affected areas and human review/verification performed. Do not include secrets,
private prompts or internal reasoning. The contributor is responsible for the diff.

## Remaining limitations

List unresolved risks, external validation and follow-up work. Do not treat local
fixtures as evidence of production capacity, cloud credentials or delivery guarantees.
