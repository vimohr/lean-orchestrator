{{common}}

# Task: repair an output file

The output `{{output_path}}` of the {{failed_role}} role does not satisfy its
contract. Validation errors:

{{errors}}

Read `{{output_path}}` if it exists and the schema `{{schema_path}}`, then rewrite
the file so that it validates while preserving its content. If the file is
missing, reconstruct it from the previous agent's final message in
`{{previous_output}}` and from files it wrote. Do not redo the research and do
not invent content. Write only `{{output_path}}`.
