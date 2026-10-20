# 22 — AI provider wire contracts and capability limits

Official documentation checked on 2026-10-19. These are implementation contracts,
not evidence of successful paid calls, model calibration or historical strategy
performance. All contract payloads in tests are synthetic. User activation is
separate from implementing an adapter; only the approved OpenAI smoke may spend
up to US$1 after its model, current price and local key are established.

| Adapter | Fixed endpoint and authentication | Request and response boundary |
| --- | --- | --- |
| OpenAI | `https://api.openai.com/v1/responses`, Bearer key | `text.format` JSON schema, explicit `max_output_tokens`, `store: false`, standard `service_tier: default`; one assistant message plus bounded reasoning metadata, exact returned model, usage and response ID. `gpt-5.6-terra` explicitly requests reasoning effort `none`. |
| xAI | `https://api.x.ai/v1/chat/completions`, Bearer key | `response_format.json_schema`, one choice, `max_completion_tokens`, explicit reasoning effort `none`. Only the documented `grok-4.3` model/effort combination is currently enabled by the capability guard. Other model IDs fail before HTTP. |
| Anthropic | `https://api.anthropic.com/v1/messages`, `x-api-key`, `anthropic-version: 2023-06-01` | `output_config.format`, explicit `max_tokens`, one assistant text block, `end_turn` or refusal, exact returned model, usage and response ID. No extended thinking or tools requested. |

Provider-supported schema subsets differ. The sent schema requires all five answer
fields and forbids extra properties, while the local frozen Pydantic contract
always enforces identity, lengths, exact positive decimal proposal prices, known
evidence references and absence of executable quantity/risk fields. Supplied
market descriptions and prior model answers are user-message data, never system
instructions. Prompt text is versioned `m5-v1`. No tool definitions, search,
attachments, broker, wallet or arbitrary destination are exposed.

The adapters use the existing locked `httpx2` dependency, injectable clients for
contract tests, fixed origins, no redirects, no automatic retries and no environment
proxy on owned clients. A total async deadline includes sending and reading; the
request is limited to 32 KiB and decoded response to 128 KiB. Unexpected compression
is refused. Complete serialized request UTF-8 byte length plus 512 must fit the
reserved input-token allowance. This is a conservative local admission rule, not a
provider tokenizer or a guarantee about undocumented provider framing. Returned
usage exceeding the allowance fails closed and remains recorded for accounting.
Provider/model/pricing capabilities must be checked again before activation.
OpenAI requests explicitly select standard service tier rather than inheriting a
project's automatic tier. Missing or different returned tiers retain an unresolved
reservation because the standard pricing snapshot cannot establish their cost.

Incomplete, malformed, tool-bearing or unknown output cannot produce a proposal.
OpenAI reasoning metadata is ignored and never executed; its tokens remain in the
provider's aggregate output usage. Provider refusal preserves known usage. HTTP
errors, timeouts, transport errors, unparseable/oversized responses and unknown
usage do not imply zero billing: they return sanitized statuses without raw error
text, and the durable spending layer retains uncertain reservations. Cancellation
is not proof that a provider stopped generating or billing.

xAI's documented `max_completion_tokens` limit covers visible output only, excluding
reasoning and function tokens. Its examples report reasoning separately from
`completion_tokens`. Consequently this adapter requires `grok-4.3` with reasoning
explicitly disabled, counts any unexpected reasoning in known output usage and
rejects the answer. Unexpected server-side source/tool usage leaves usage unknown
because a token-only tariff cannot safely account for those costs. No paid xAI
activation is authorized. The provider's named family/version is recorded exactly;
we do not claim immutable internal model weights or silently follow retired aliases.

Anthropic cache reads are included in total input and charged conservatively at the
configured normal input tariff. Cache writes are not requested. Unexpected nonzero
cache-creation usage fails closed with unknown usage because its higher write tariff
is not represented by the ordinary token rate. Cost records are conservative
estimates with pricing provenance, never asserted provider invoices.

## Official references

- [OpenAI structured outputs](https://developers.openai.com/api/docs/guides/structured-outputs?api-mode=responses): Responses JSON schema, refusal and incomplete-output handling.
- [OpenAI Responses create reference](https://developers.openai.com/api/reference/cli/resources/responses/methods/create): `service_tier: default` selects standard pricing; the returned tier can differ and is checked before usage can settle.
- [OpenAI GPT-5.6 Terra model](https://developers.openai.com/api/docs/models/gpt-5.6-terra): supported model capabilities. Exact current pricing is recorded by the activation workflow separately.
- [xAI chat-completions reference](https://docs.x.ai/developers/rest-api-reference/inference/chat-completions): request, usage and visible-output cap. Its linked Markdown representation was also fetched to inspect optional request fields omitted by the rendered documentation view.
- [xAI structured outputs](https://docs.x.ai/developers/model-capabilities/text/structured-outputs): JSON schema request format.
- [xAI reasoning](https://docs.x.ai/developers/model-capabilities/text/reasoning), [best practices](https://docs.x.ai/developers/model-capabilities/text/best-practices) and [retirement guide](https://docs.x.ai/developers/migration/may-15-retirement): explicit effort selection, documented `grok-4.3` nonreasoning mode and unsafe implicit alias migration.
- [Anthropic structured outputs](https://platform.claude.com/docs/en/build-with-claude/structured-outputs): raw schema, unsupported constraints, refusal and billed truncation.
- [Anthropic Messages reference](https://platform.claude.com/docs/en/api/messages/create): authentication, message blocks, usage and cache fields.

The initial generic OpenAI Responses create reference could not be fetched through
the documentation browser; its CLI reference was subsequently fetched to verify
service-tier semantics, alongside the structured-output guide. Deterministic tests do not establish provider account
access, production compatibility or exact invoiced cost. The final real OpenAI smoke
is separate acceptance evidence through the same adapter and durable budget path.
