// SPDX-License-Identifier: AGPL-3.0-or-later
package ai.comma.signallab;

import android.content.Context;
import android.graphics.Bitmap;
import android.graphics.Canvas;
import android.graphics.Color;
import android.graphics.Paint;
import android.graphics.Rect;
import java.io.InputStream;
import java.nio.ByteBuffer;
import java.nio.ByteOrder;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.List;
import org.tensorflow.lite.Interpreter;
import org.tensorflow.lite.DataType;

final class KoreanDetector implements AutoCloseable {
    static final String SHA="458c8f1273ae407e6fa8334159a7f6dc29c88a2680b15eae2771105b0bdeffc2";
    static final int SIZE=640, ROWS=8400;
    final Interpreter interpreter;
    final ByteBuffer input=ByteBuffer.allocateDirect(1*SIZE*SIZE*3*4).order(ByteOrder.nativeOrder());
    final ByteBuffer output=ByteBuffer.allocateDirect(9*ROWS*4).order(ByteOrder.nativeOrder());
    final int[] pixels=new int[SIZE*SIZE];
    final float[] result=new float[9*ROWS];
    final Bitmap resized=Bitmap.createBitmap(SIZE,SIZE,Bitmap.Config.ARGB_8888);
    final Canvas canvas=new Canvas(resized);
    final Paint paint=new Paint(Paint.FILTER_BITMAP_FLAG);
    KoreanDetector(Context context) throws Exception {
        byte[] data;
        try(InputStream stream=context.getAssets().open("korean-signal.tflite")) { data=readAll(stream); }
        byte[] digest=java.security.MessageDigest.getInstance("SHA-256").digest(data);
        StringBuilder hex=new StringBuilder();for(byte b:digest)hex.append(String.format(java.util.Locale.ROOT,"%02x",b&255));
        if(!SHA.equals(hex.toString()))throw new IllegalStateException("모델 체크섬 불일치");
        ByteBuffer model=ByteBuffer.allocateDirect(data.length).order(ByteOrder.nativeOrder());model.put(data);model.rewind();
        interpreter=new Interpreter(model,new Interpreter.Options().setNumThreads(4));
        if(!Arrays.equals(interpreter.getInputTensor(0).shape(),new int[]{1,SIZE,SIZE,3})
                ||!Arrays.equals(interpreter.getOutputTensor(0).shape(),new int[]{1,9,ROWS})
                ||interpreter.getInputTensor(0).dataType()!=DataType.FLOAT32||interpreter.getOutputTensor(0).dataType()!=DataType.FLOAT32) {
            interpreter.close();throw new IllegalStateException("한국 모델 입력·출력 형식 불일치");
        }
    }
    static byte[] readAll(InputStream in)throws Exception {
        java.io.ByteArrayOutputStream out=new java.io.ByteArrayOutputStream();byte[] b=new byte[16384];int n;
        while((n=in.read(b))!=-1){if(out.size()+n>16*1024*1024)throw new IllegalArgumentException("모델 크기 초과");out.write(b,0,n);}return out.toByteArray();
    }
    List<Decoder.Detection> detect(Bitmap frame) {
        List<Decoder.Detection> all=new ArrayList<>();
        int w=frame.getWidth(),h=frame.getHeight();
        // Two overlapping road tiles preserve far-away housing detail better
        // than shrinking the entire wide image to 640. No temporal frame queue.
        int cw=w>SIZE?Math.max(SIZE,(int)Math.ceil(w*.60)):w;
        int[] offsets=cw<w?new int[]{0,w-cw}:new int[]{0};
        for(int offset:offsets) {
            float ratio=Math.min(SIZE/(float)cw,SIZE/(float)h);
            int nw=Math.max(1,Math.round(cw*ratio)),nh=Math.max(1,Math.round(h*ratio));
            int px=(SIZE-nw)/2,py=(SIZE-nh)/2;
            canvas.drawColor(Color.rgb(114,114,114));
            canvas.drawBitmap(frame,new Rect(offset,0,offset+cw,h),new Rect(px,py,px+nw,py+nh),paint);
            resized.getPixels(pixels,0,SIZE,0,0,SIZE,SIZE);
            input.clear();
            for(int p:pixels){input.putFloat(((p>>16)&255)/255f);input.putFloat(((p>>8)&255)/255f);input.putFloat((p&255)/255f);}
            input.rewind();output.clear();interpreter.run(input,output);output.rewind();output.asFloatBuffer().get(result);
            all.addAll(Decoder.decode(result,ROWS,ratio,px,py,offset,0,cw,h,w,h));
        }
        return Decoder.suppress(all);
    }
    @Override public void close(){interpreter.close();resized.recycle();}
}
