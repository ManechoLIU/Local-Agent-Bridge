#!/usr/bin/env python3
"""A/B the original grouped-RPC403 while retaining exact replay rejection.

Uses only test-specific HOME, capability authority, keys, sockets and loopback fixture.
Never loads production environment, authority keys, configuration or Controller state.
Requires the original defective and fixed frozen binaries supplied by the operator.
"""
from pathlib import Path
import argparse,json,hashlib,os,tempfile,threading,http.server,http.client,socket,subprocess,time,shutil,secrets,datetime
parser=argparse.ArgumentParser(description="Reproduce provider-group replay collisions with two frozen Bridge binaries on isolated private UDS; never touch production state.")
parser.add_argument("--old-binary",type=Path,required=True)
parser.add_argument("--fixed-binary",type=Path,required=True)
parser.add_argument("--read-path",type=Path,required=True,help="Existing harmless file to read with identical paths arguments")
parser.add_argument("--output",type=Path,help="Optional new JSON report; refuses overwriting existing evidence")
args=parser.parse_args()
old=args.old_binary.expanduser().resolve(strict=True);new=args.fixed_binary.expanduser().resolve(strict=True);read_path=args.read_path.expanduser().resolve(strict=True)
if not read_path.is_file() or not all(p.is_file() and os.access(p,os.X_OK) for p in (old,new)):
 parser.error("require executable frozen binaries and a regular harmless read file")
if args.output and (args.output.exists() or args.output.is_symlink()):parser.error("output exists; preserve original evidence")
root=Path(tempfile.mkdtemp(prefix='lab403-',dir='/tmp'));root.chmod(0o700);hits=[]
class Backend(http.server.BaseHTTPRequestHandler):
 def do_POST(self):
  b=self.rfile.read(int(self.headers['Content-Length']));v=json.loads(b);hits.append({'rpc_id':v.get('id'),'method':v.get('method'),'path':self.path});reply=json.dumps({'jsonrpc':'2.0','id':v.get('id'),'result':{'content':[{'type':'text','text':read_path.read_text()}],'isError':False}}).encode();self.send_response(200);self.send_header('Content-Type','application/json');self.send_header('Content-Length',str(len(reply)));self.end_headers();self.wfile.write(reply)
 def log_message(self,*a):pass
server=http.server.ThreadingHTTPServer(('127.0.0.1',0),Backend);threading.Thread(target=server.serve_forever,daemon=True).start()
class Unix(http.client.HTTPConnection):
 def __init__(self,path):super().__init__('localhost',timeout=5);self.path=path
 def connect(self):self.sock=socket.socket(socket.AF_UNIX,socket.SOCK_STREAM);self.sock.settimeout(5);self.sock.connect(str(self.path))
group='d3697cba-634c-4fd9-823c-1f57a995b54a';rpc1='dbdacf56-267f-413e-b767-8b45be70aefc';rpc2='028db034-0f84-4833-b988-25e0e2f50cc4';processes=[];results=[]
def start(exe,env,sock):
 log=(Path(env['LAB_BRIDGE_STATE_DIR']).parent/'stderr.log').open('ab');p=subprocess.Popen([str(exe)],env=env,stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=log);processes.append((p,log));deadline=time.monotonic()+6
 while time.monotonic()<deadline and not sock.exists() and p.poll() is None:time.sleep(.02)
 if p.poll() is not None or not sock.exists():raise RuntimeError('isolated fixture failed to start: '+(Path(env['LAB_BRIDGE_STATE_DIR']).parent/'stderr.log').read_text()[-1000:])
 return p
def stop(p):
 p.terminate()
 try:p.wait(timeout=3)
 except subprocess.TimeoutExpired:p.kill();p.wait(timeout=2)
def request(sock,rpc):
 v={'jsonrpc':'2.0','id':rpc,'method':'tools/call','params':{'name':'read','arguments':{'paths':[str(read_path)]}}};c=Unix(sock);c.request('POST','/mcp',body=json.dumps(v).encode(),headers={'Content-Type':'application/json','mcp-method':'tools/call','mcp-name':'read','x-request-id':group});response=c.getresponse();raw=response.read(131072);status=response.status;ctype=response.getheader('content-type');c.close();body=json.loads(raw)
 return {'http_status':status,'content_type':ctype,'rpc_id':rpc,'backend_hits':len(hits),'error':body.get('error'),'response_sha256':hashlib.sha256(raw).hexdigest()}
try:
 for label,exe in [('original_g34',old),('installed_rpc_v2',new)]:
  d=root/label;d.mkdir(mode=0o700);key=d/'backend.key';key.write_text(secrets.token_hex(32));key.chmod(0o600);sock=d/'bridge.sock';env={'PATH':os.environ['PATH'],'HOME':str(d),'LAB_BRIDGE_SOCKET':str(sock),'LAB_BRIDGE_BACKEND_URL':f'http://127.0.0.1:{server.server_address[1]}/mcp','LAB_BRIDGE_BACKEND_KEY_FILE':str(key),'LAB_BRIDGE_STATE_DIR':str(d/'authority'),'LAB_BRIDGE_WORKSPACE_ID':'test403-workspace','LAB_BRIDGE_PRINCIPAL_ID':'test403-principal','LAB_BRIDGE_EXECUTION_SESSION_ID':'test403-session','LAB_BRIDGE_CAPABILITY_ID':'test403-capability','LAB_BRIDGE_CAPABILITY_TTL_MS':'60000','LAB_BRIDGE_CAPABILITIES':'discovery,file_read'}
  proc=start(exe,env,sock);base_hits=len(hits);first=request(sock,rpc1);second=request(sock,rpc2);exact_duplicate_rpc=request(sock,rpc1);stop(proc);sock.unlink(missing_ok=True);proc=start(exe,env,sock);after_restart=request(sock,rpc1);fresh_after_restart=request(sock,'fresh-after-restart');stop(proc)
  assert first['http_status']==200
  assert second['http_status']==(403 if label=='original_g34' else 200)
  assert exact_duplicate_rpc['http_status']==403 and after_restart['http_status']==403
  assert fresh_after_restart['http_status']==(403 if label=='original_g34' else 200)
  assert exact_duplicate_rpc['backend_hits']==second['backend_hits']
  assert after_restart['backend_hits']==exact_duplicate_rpc['backend_hits']
  if label=='original_g34':assert second['backend_hits']==first['backend_hits'] and fresh_after_restart['backend_hits']==after_restart['backend_hits']
  if label=='original_g34':assert second['error']['message']=='request id replay detected' and second['error']['code']==-32001
  results.append({'variant':label,'binary':str(exe),'binary_sha256':hashlib.sha256(exe.read_bytes()).hexdigest(),'first':first,'distinct_rpc_in_same_group':second,'exact_duplicate_rpc':exact_duplicate_rpc,'duplicate_after_restart':after_restart,'fresh_rpc_after_restart':fresh_after_restart,'backend_delta':len(hits)-base_hits})
 out={'status':'PASS_OLD_FAILURE_REPRODUCED_AND_INSTALLED_FIX_VERIFIED','observed_at':datetime.datetime.now(datetime.timezone.utc).isoformat(),'same_original_provider_group':group,'same_original_first_and_failed_rpc_ids':[rpc1,rpc2],'identical_read_arguments':{'paths':[str(read_path)]},'test_scope':'Actual original and installed production binaries; isolated privateUDS/mock backend and fresh test-only capability authority; zero production writes or restarts, no production key read/rotation, no external model requests.','results':results};payload=json.dumps(out,ensure_ascii=False,indent=2)+'\n'
 if args.output:
  fd=os.open(args.output,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600)
  with os.fdopen(fd,'w') as f:f.write(payload)
 print(payload)
finally:
 for p,log in processes:
  if p.poll() is None:stop(p)
  log.close()
 server.shutdown();server.server_close();shutil.rmtree(root)
