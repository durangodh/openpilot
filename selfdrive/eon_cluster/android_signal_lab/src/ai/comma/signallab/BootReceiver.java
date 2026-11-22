// SPDX-License-Identifier: AGPL-3.0-or-later
package ai.comma.signallab;

import android.content.BroadcastReceiver;
import android.content.Context;
import android.content.Intent;

/** Starts recognition when the phone boots, once paired (2026-11-22: runs every drive). */
public final class BootReceiver extends BroadcastReceiver {
    @Override public void onReceive(Context context,Intent intent){
        String host=context.getSharedPreferences("signal",0).getString("host","");
        String token=context.getSharedPreferences("signal",0).getString("token","");
        if(host.isEmpty()||!token.matches("[0-9]{4}"))return;
        try{context.startForegroundService(new Intent(context,SignalService.class));}catch(Exception ignored){}
    }
}
