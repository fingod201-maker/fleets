# NOTES — verified Sliver symbols

Every symbol below was copied out of the extracted module source at

```
$(go env GOMODCACHE)/github.com/bishopfox/sliver@v1.7.9
```

via `grep -rn "func (c \*..." ...` / struct extraction. **Nothing here is
invented.** If a symbol is not listed, it was not used.

## Dial layer — `client/transport/mtls.go`

```go
func MTLSConnect(config *assets.ClientConfig) (rpcpb.SliverRPCClient, *grpc.ClientConn, error)
func MTLSConnectWithStatus(config *assets.ClientConfig, statusFn ConnectStatusFn) (rpcpb.SliverRPCClient, *grpc.ClientConn, error)
```

- `rpcpb` alias here = `github.com/bishopfox/sliver/protobuf/rpcpb`.
- SPEC §3 says `func (c *SliverClient)`; the real dial surface is these two free
  functions (there is no `SliverClient` type in `client/transport`; the type
  `SliverClient` lives in `client/console` and is REPL-oriented). Recorded in
  `DECISIONS.md`.

## Operator config — `client/assets/config.go`

```go
type ClientConfig struct {
    Operator, LHost string
    LPort int
    Token, CACertificate, PrivateKey, Certificate string
    WG *ClientWGConfig
}
func ReadConfig(confFilePath string) (*ClientConfig, error)
```

## Service — `protobuf/rpcpb/services_grpc.pb.go`, `type SliverRPCClient interface`

Exact signatures used (context/opts elided only for width; they are
`ctx context.Context, in *T, opts ...grpc.CallOption`):

```go
GetVersion(ctx, *commonpb.Empty) (*clientpb.Version, error)
GetOperators(ctx, *commonpb.Empty) (*clientpb.Operators, error)
GetSessions(ctx, *commonpb.Empty) (*clientpb.Sessions, error)
GetBeacons(ctx, *commonpb.Empty) (*clientpb.Beacons, error)
Events(ctx, *commonpb.Empty) (grpc.ServerStreamingClient[clientpb.Event], error)
Generate(ctx, *clientpb.GenerateReq) (*clientpb.Generate, error)
Shell(ctx, *sliverpb.ShellReq) (*sliverpb.Shell, error)
Execute(ctx, *sliverpb.ExecuteReq) (*sliverpb.Execute, error)
Ps(ctx, *sliverpb.PsReq) (*sliverpb.Ps, error)
Terminate(ctx, *sliverpb.TerminateReq) (*sliverpb.Terminate, error)
Ls(ctx, *sliverpb.LsReq) (*sliverpb.Ls, error)
Upload(ctx, *sliverpb.UploadReq) (*sliverpb.Upload, error)
Download(ctx, *sliverpb.DownloadReq) (*sliverpb.Download, error)
Ifconfig(ctx, *sliverpb.IfconfigReq) (*sliverpb.Ifconfig, error)
GetSystem(ctx, *clientpb.GetSystemReq) (*sliverpb.GetSystem, error)
Rename(ctx, *clientpb.RenameReq) (*commonpb.Empty, error)
Kill(ctx, *sliverpb.KillReq) (*commonpb.Empty, error)
CreateTunnel(ctx, *sliverpb.Tunnel) (*sliverpb.Tunnel, error)
CloseTunnel(ctx, *sliverpb.Tunnel) (*commonpb.Empty, error)
TunnelData(ctx) (grpc.BidiStreamingClient[sliverpb.TunnelData, sliverpb.TunnelData], error)
```

## Messages used

```go
// commonpb
type Empty struct{}
type Request struct{ Async bool; Timeout int64; BeaconID, SessionID string }
type Response struct{ Err string; Async bool; BeaconID, TaskID string }
type Process struct{ Pid, Ppid int32; Executable, Owner, Architecture string; SessionID int32; CmdLine []string }
type File struct{ Name string; Data []byte }

// clientpb
type Version struct{ Major, Minor, Patch int32; Commit string; Dirty bool; CompiledAt int64; OS, Arch string }
type Session struct{ ID, Name, Hostname, UUID, Username, UID, GID, OS, Arch, Transport, RemoteAddress string; PID int32; Filename string; LastCheckin int64; ActiveC2, Version string; Evasion, IsDead bool; ReconnectInterval int64; ProxyURL string; Burned bool; Extensions []string; PeerID int64; Locale string; FirstContact int64; Integrity string; Capabilities uint64 }
type Sessions struct{ Sessions []*Session }
type Beacons struct{ Beacons []*Beacon }
type Beacon struct{ /* see source; used read-only */ }
type Event struct{ EventType string; Session *Session; Job *Job; Client *Client; Data []byte; Err string }
type RenameReq struct{ SessionID, BeaconID, Name string }
type GenerateReq struct{ Config *ImplantConfig; Name string }
type Generate struct{ File *commonpb.File; ImplantName, ImplantBuildID string }
type ImplantConfig struct{ /* full field list in DECISIONS/notes; key fields: */ IsBeacon bool; BeaconInterval, BeaconJitter int64; GOOS, GOARCH string; Evasion, ObfuscateSymbols, SGNEnabled bool; IncludeMTLS, IncludeHTTP, IncludeNamePipe, IncludeTCP bool; Format OutputFormat; IsShellcode bool; C2 []*ImplantC2; Assets []*commonpb.File; ShellcodeEncoder ShellcodeEncoder }
type ImplantC2 struct{ ID string; Priority uint32; URL, Options string }
type ImplantBuilds struct{ Configs map[string]*ImplantConfig }
type OutputFormat int32 // SHARED_LIB=0 SHELLCODE=1 EXECUTABLE=2 SERVICE=3 THIRD_PARTY=4 GO_ARCHIVE=5
type ShellcodeEncoder int32 // NONE=0 SHIKATA_GA_NAI=1 XOR=2 XOR_DYNAMIC=3
type MTLSListenerReq struct{ Host string; Port uint32 }
type ListenerJob struct{ ID, Type string; JobID uint32; MTLSConf *MTLSListenerReq; ... }

// sliverpb
type ShellReq struct{ Path string; EnablePTY bool; Pid, Rows, Cols uint32; TunnelID uint64; Request *commonpb.Request }
type Shell struct{ Path string; EnablePTY bool; Pid uint32; TunnelID uint64; Response *commonpb.Response }
type ExecuteReq struct{ Path string; Args []string; Output bool; Stdout, Stderr string; EnvInheritance bool; Env map[string]string; Background bool; PPid uint32; Request *commonpb.Request }
type Execute struct{ Status uint32; Stdout, Stderr []byte; Pid uint32; Response *commonpb.Response }
type LsReq struct{ Path string; Request *commonpb.Request }
type Ls struct{ Path string; Exists bool; Files []*FileInfo; Timezone string; TimezoneOffset int32; Response *commonpb.Response }
type FileInfo struct{ Name string; IsDir bool; Size int64; ModTime int64; Mode, Link, Uid, Gid string }
type UploadReq struct{ Path, Encoder string; Data []byte; IsIOC bool; FileName string; IsDirectory, Overwrite bool; Request *commonpb.Request }
type Upload struct{ Path string; WrittenFiles, UnwriteableFiles int32; Response *commonpb.Response }
type DownloadReq struct{ Path string; Start, Stop int64; Recurse bool; MaxBytes, MaxLines int64; RestrictedToFile bool; Request *commonpb.Request }
type Download struct{ Path, Encoder string; Exists bool; Start, Stop int64; Data []byte; IsDir bool; ReadFiles, UnreadableFiles int32; Response *commonpb.Response }
type PsReq struct{ FullInfo bool; Request *commonpb.Request }
type Ps struct{ Processes []*commonpb.Process; Response *commonpb.Response }
type TerminateReq struct{ Pid int32; Force bool; Request *commonpb.Request }
type Terminate struct{ Pid int32; Response *commonpb.Response }
type IfconfigReq struct{ Request *commonpb.Request }
type Ifconfig struct{ NetInterfaces []*NetInterface; Response *commonpb.Response }
type NetInterface struct{ Index int32; Name, MAC string; IPAddresses []string }
type GetSystem struct{ /* fields on response */ Response *commonpb.Response }
type KillReq struct{ Force bool; Request *commonpb.Request }
type Tunnel struct{ TunnelID uint64; SessionID string }
type TunnelData struct{ Data []byte; Closed bool; Sequence, Ack uint64; Resend, CreateReverse bool; TunnelID uint64; SessionID string }
const MaxTunnelFrameBytes = 64 * 1024
```

## Event type strings — `client/constants/constants.go`

```go
SessionOpenedEvent = "session-connected"
SessionClosedEvent = "session-disconnected"
SessionUpdateEvent = "session-updated"
BeaconRegisteredEvent = "beacon-registered"
JobStartedEvent = "job-started"
JobStoppedEvent = "job-stopped"
```

## Tunnels — `client/core`

```go
func TunnelLoop(ctx context.Context, rpc rpcpb.SliverRPCClient) error
func GetTunnels() *tunnels
func (t *tunnels) Start(tunnelID uint64, sessionID string) *TunnelIO
// TunnelIO: io.ReadWriter; methods Write/Read/Close/Done()/Bound()/IsOpen(); fields Send chan []byte, Recv chan []byte
```

## Teamserver CLI (verified against the v1.7.9 release binary)

```
sliver-server daemon  -l <host> -p <port>     # headless teamserver, multiplayer listener
sliver-server operator -l <host> -p <port> -n <name> --save <path> -P all
```
