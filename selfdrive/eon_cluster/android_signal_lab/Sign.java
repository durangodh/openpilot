import com.android.apksig.ApkSigner;
import com.android.apksig.ApkVerifier;
import java.io.*;
import java.security.*;
import java.security.cert.X509Certificate;
import java.util.*;
class Sign {
 public static void main(String[] args)throws Exception{
  KeyStore ks=KeyStore.getInstance("PKCS12");try(InputStream in=new FileInputStream(args[0])){ks.load(in,"android".toCharArray());}
  var key=(PrivateKey)ks.getKey("signallab","android".toCharArray());
  var cert=(X509Certificate)ks.getCertificate("signallab");
  var config=new ApkSigner.SignerConfig.Builder("signallab",key,Collections.singletonList(cert)).build();
  new ApkSigner.Builder(Collections.singletonList(config)).setInputApk(new File(args[1])).setOutputApk(new File(args[2]))
   .setMinSdkVersion(30).setV1SigningEnabled(false).setV2SigningEnabled(true).setV3SigningEnabled(true).build().sign();
  var result=new ApkVerifier.Builder(new File(args[2])).setMinCheckedPlatformVersion(30).build().verify();
  if(!result.isVerified())throw new IllegalStateException(result.getErrors().toString());
  System.out.println("APK signature verified: v2="+result.isVerifiedUsingV2Scheme()+" v3="+result.isVerifiedUsingV3Scheme());
 }
}
