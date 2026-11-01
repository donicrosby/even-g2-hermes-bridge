## REMOVED Requirements

### Requirement: Single .proto file is the source of truth for the wire format

### Requirement: Frame wire format is a single Protobuf `Frame` with a `oneof payload`

### Requirement: Audio PCM is wrapped in an AudioData Frame variant

### Requirement: Codegen pipeline generates Python and TypeScript stubs

### Requirement: wire.py and wire.ts wrappers preserve constructor call-site names

### Requirement: No transport change