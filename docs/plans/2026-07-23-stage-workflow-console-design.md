# Stage Workflow Console Design

Stage is an enterprise service governance runtime, not a free-form workflow builder.
The console should show every service request as a governed delivery flow: request
understanding, service domain, route, execution boundary, permissions, evidence,
verification, safety, closure, and audit.

The first implementation is a read-only workflow canvas. Users can scan node
status from left to right and select any node to inspect its input, output,
decision reasons, constraints, and audit-relevant payload. Later task families
such as question answering, access requests, code changes, on-call, SRE changes,
and approvals remain instances of the same governance chain rather than separate
products.

External knowledge content and concrete wiki paths stay out of code. The UI only
renders runtime results returned by the backend.
