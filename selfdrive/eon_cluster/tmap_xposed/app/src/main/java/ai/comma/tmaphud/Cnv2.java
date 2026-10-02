package ai.comma.tmaphud;

import java.nio.ByteBuffer;
import java.nio.ByteOrder;

/**
 * carrot_navi_server 의 40바이트 CNV2 바이너리 헤더(">4sBBBBIIQQIHH") + 본문.
 * 패치판 티맵이 map_main·이미지 스트림에 붙이던 형식과 같다.
 */
final class Cnv2 {
    static final int PROTOCOL_VERSION = 2;
    static final int TYPE_IMAGE = 1;
    static final int TYPE_CLEAR = 4;
    static final int FORMAT_PNG = 0;
    // 서버 update_map: CNV2 format 2 = JPEG.
    static final int FORMAT_JPEG = 2;
    static final int HEADER_BYTES = 40;

    private Cnv2() {
    }

    static byte[] frame(int messageType, int format, long sequence, byte[] body, int width, int height) {
        int bodyLen = body == null ? 0 : body.length;
        ByteBuffer buf = ByteBuffer.allocate(HEADER_BYTES + bodyLen).order(ByteOrder.BIG_ENDIAN);
        buf.put((byte) 'C').put((byte) 'N').put((byte) 'V').put((byte) '2');
        buf.put((byte) PROTOCOL_VERSION);
        buf.put((byte) messageType);
        buf.put((byte) format);
        buf.put((byte) 0);                       // flags
        buf.putInt(0);                           // stream_handle
        buf.putInt(0);                           // revision
        buf.putLong(sequence);
        buf.putLong(System.currentTimeMillis()); // source_timestamp_ms
        buf.putInt(bodyLen);
        buf.putShort((short) width);
        buf.putShort((short) height);
        if (body != null) buf.put(body);
        return buf.array();
    }
}
