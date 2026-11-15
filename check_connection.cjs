// Read-only end-to-end check. Access key URL is supplied at runtime, never logged.
const {createHash,createCipheriv,createDecipheriv,randomBytes,hkdfSync}=require('node:crypto');
const nonce=n=>{const b=Buffer.alloc(12);b.writeUInt32LE(n);return b;};
function master(password){let out=Buffer.alloc(0),part=Buffer.alloc(0);while(out.length<32){part=createHash('md5').update(Buffer.concat([part,Buffer.from(password)])).digest();out=Buffer.concat([out,part]);}return out.subarray(0,32);}
function subkey(key,salt){return Buffer.from(hkdfSync('sha1',key,salt,Buffer.from('ss-subkey'),32));}
function encrypt(key,n,data){const c=createCipheriv('chacha20-poly1305',key,nonce(n),{authTagLength:16});return Buffer.concat([c.update(data),c.final(),c.getAuthTag()]);}
function decrypt(key,n,data){const d=createDecipheriv('chacha20-poly1305',key,nonce(n),{authTagLength:16});d.setAuthTag(data.subarray(-16));return Buffer.concat([d.update(data.subarray(0,-16)),d.final()]);}
async function main(){
 const response=await fetch(process.argv[2],{signal:AbortSignal.timeout(10000)});
 if(!response.ok)throw new Error('Access configuration HTTP '+response.status);
 const config=await response.json(), transport=config.transport.tcp;
 const key=master(transport.secret),salt=randomBytes(32),session=subkey(key,salt);
 const host=Buffer.from('example.com'),port=Buffer.from([0,80]);
 const request=Buffer.concat([Buffer.from([3,host.length]),host,port,Buffer.from('GET / HTTP/1.1\r\nHost: example.com\r\nConnection: close\r\n\r\n')]);
 const length=Buffer.alloc(2);length.writeUInt16BE(request.length);
 const payload=Buffer.concat([salt,encrypt(session,0,length),encrypt(session,1,request)]);
 await new Promise((resolve,reject)=>{
  const ws=new WebSocket(transport.endpoint.url);ws.binaryType='arraybuffer';let buffer=Buffer.alloc(0),receiveKey=null,n=0,pending=null,done=false;
  const timer=setTimeout(()=>finish(new Error('VPN response timeout')),15000);
  function finish(error){if(done)return;done=true;clearTimeout(timer);ws.close();error?reject(error):resolve();}
  ws.onopen=()=>ws.send(payload);
  ws.onerror=()=>finish(new Error('WebSocket connection failed'));
  ws.onclose=()=>{if(!done)finish(new Error('Connection closed before an authenticated response'));};
  ws.onmessage=event=>{try{
   buffer=Buffer.concat([buffer,Buffer.from(event.data)]);
   if(!receiveKey){if(buffer.length<32)return;receiveKey=subkey(key,buffer.subarray(0,32));buffer=buffer.subarray(32);}
   while(true){
    if(pending===null){if(buffer.length<18)return;pending=decrypt(receiveKey,n++,buffer.subarray(0,18)).readUInt16BE();buffer=buffer.subarray(18);}
    if(buffer.length<pending+16)return;
    const plain=decrypt(receiveKey,n++,buffer.subarray(0,pending+16));buffer=buffer.subarray(pending+16);pending=null;
    if(plain.toString().startsWith('HTTP/1.')){console.log('PASS: authenticated TCP-over-WebSocket VPN returned an HTTP response');finish();return;}
   }
  }catch(error){finish(new Error('Authenticated VPN response could not be decoded'));}};
 });
}
main().catch(error=>{console.error(error.message);process.exitCode=1;});
