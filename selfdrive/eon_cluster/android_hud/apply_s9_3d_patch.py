from pathlib import Path

path = Path('selfdrive/eon_cluster/android_hud/app/src/main/java/ai/comma/remotehud/HudService.java')
text = path.read_text(encoding='utf-8')


def replace_between(start, end, replacement):
    global text
    s = text.find(start)
    if s < 0:
        raise SystemExit(f'missing start anchor: {start}')
    e = text.find(end, s)
    if e < 0:
        raise SystemExit(f'missing end anchor: {end}')
    text = text[:s] + replacement.rstrip() + '\n' + text[e:]


# Runtime tuning runs first. It adds buildings/road edges and the pathCenterAt()
# helper. This pass then makes road, lanes, path, lead cars, ego and BSD share
# the same curved perspective coordinate system.
new_draw_world = r'''    private void drawWorld(Canvas c,Paint p,JSONObject s,boolean enabled){
        JSONObject l=layout(s);final float top=217,bottom=454,cx=DRIVE_RIGHT/2.0f;
        p.setShader(null);p.setStyle(Paint.Style.FILL);p.setColor(lc(l,"driveBg",Color.rgb(239,241,242)));c.drawRect(0,top,DRIVE_RIGHT-3,bottom,p);
        JSONArray path=s.optJSONArray("path");
        drawRoadSurface3D(c,p,path,cx,top,bottom,lc(l,"roadTop",Color.rgb(226,229,231)),lc(l,"roadBottom",Color.rgb(216,220,223)));
        drawRoadGrid3D(c,p,path,cx,top,bottom);
        drawBuildings(c,p,s,cx,top,bottom);
        JSONArray edges=s.optJSONArray("edges");if(edges!=null)for(int i=0;i<edges.length();i++){JSONObject edge=edges.optJSONObject(i);if(edge==null||edge.optDouble("c",0)<0.18)continue;JSONArray pts=edge.optJSONArray("p");if(pts!=null)drawWorldLine(c,p,pts,cx,top,bottom,Color.rgb(151,160,166),3.0f,false);}
        JSONArray lanes=s.optJSONArray("lanes");if(lanes!=null)for(int i=0;i<lanes.length();i++){JSONObject lane=lanes.optJSONObject(i);if(lane==null)continue;JSONArray pts=lane.optJSONArray("p");if(pts!=null)drawWorldLine(c,p,pts,cx,top,bottom,Color.rgb(250,251,251),2.5f,true);}
        if(enabled&&path!=null&&path.length()>1)drawPath(c,p,path,cx,top,bottom,lc(l,"pathColor",Color.rgb(24,126,224)));
        JSONObject lead2=s.optJSONObject("lead2");if(lead2!=null)drawLeadVehicle(c,p,lead2,path,cx,top,bottom,false);
        JSONObject lead=s.optJSONObject("lead");if(lead!=null)drawLeadVehicle(c,p,lead,path,cx,top,bottom,true);
        float egoAngle=pathAngleAt(path,4.0f,cx,top,bottom);drawVehicle3D(c,p,egoCar,cx,414,78,egoAngle,255,1.0f);
        float bsdD=3.2f,center=pathCenterAt(path,bsdD);
        if(s.optBoolean("leftBsd",false)){float[] q=project(bsdD,center+2.85f,cx,top,bottom);drawVehicle3D(c,p,otherCar,q[0],q[1]+16,46,pathAngleAt(path,bsdD,cx,top,bottom)-16,238,0.82f);}
        if(s.optBoolean("rightBsd",false)){float[] q=project(bsdD,center-2.85f,cx,top,bottom);drawVehicle3D(c,p,otherCar,q[0],q[1]+16,46,pathAngleAt(path,bsdD,cx,top,bottom)+16,238,0.82f);}
        if(s.optBoolean("leftBlinker",false)&&((SystemClock.elapsedRealtime()/500)&1)==0)drawBlinker(c,p,245,386,true);
        if(s.optBoolean("rightBlinker",false)&&((SystemClock.elapsedRealtime()/500)&1)==0)drawBlinker(c,p,523,386,false);
    }
'''
replace_between('    private void drawWorld(Canvas c,Paint p,JSONObject s,boolean enabled){', '    private float[] project(', new_draw_world)

new_world_helpers = r'''    private void drawWorldLine(Canvas c,Paint p,JSONArray pts,float cx,float top,float bottom,int color,float width,boolean dashed){
        p.setStyle(Paint.Style.FILL);p.setShader(null);p.setColor(color);
        for(int i=0;i<pts.length()-1;i++){
            JSONArray a=pts.optJSONArray(i),b=pts.optJSONArray(i+1);if(a==null||b==null)continue;
            float x1=(float)a.optDouble(0),x2=(float)b.optDouble(0);if(dashed&&(((int)((x1+x2)*0.5f/5.0f))&1)!=0)continue;
            float[] pa=project(x1,(float)a.optDouble(1),cx,top,bottom),pb=project(x2,(float)b.optDouble(1),cx,top,bottom);
            float near=1.0f-Math.min(1.0f,Math.max(0.0f,(x1+x2)*0.5f/120.0f));float w=Math.max(1.3f,width*(0.68f+1.75f*near));
            drawPerspectiveSegment(c,p,pa,pb,w,w*0.84f);
        }
    }
    private void drawPerspectiveSegment(Canvas c,Paint p,float[] a,float[] b,float wa,float wb){
        float dx=b[0]-a[0],dy=b[1]-a[1],len=(float)Math.hypot(dx,dy);if(len<0.5f)return;float nx=-dy/len,ny=dx/len,ha=wa*0.5f,hb=wb*0.5f;Path q=new Path();q.moveTo(a[0]+nx*ha,a[1]+ny*ha);q.lineTo(b[0]+nx*hb,b[1]+ny*hb);q.lineTo(b[0]-nx*hb,b[1]-ny*hb);q.lineTo(a[0]-nx*ha,a[1]-ny*ha);q.close();c.drawPath(q,p);
    }
    private void drawRoadSurface3D(Canvas c,Paint p,JSONArray path,float cx,float top,float bottom,int topColor,int bottomColor){
        final int n=18;float[][] left=new float[n][],right=new float[n][],outerL=new float[n][],outerR=new float[n][];
        for(int i=0;i<n;i++){float u=i/(float)(n-1),d=2.0f+118.0f*u*u;float center=pathCenterAt(path,d);left[i]=project(d,center+4.45f,cx,top,bottom);right[i]=project(d,center-4.45f,cx,top,bottom);outerL[i]=project(d,center+5.10f,cx,top,bottom);outerR[i]=project(d,center-5.10f,cx,top,bottom);}
        Path road=new Path();road.moveTo(left[0][0],left[0][1]);for(int i=1;i<n;i++)road.lineTo(left[i][0],left[i][1]);for(int i=n-1;i>=0;i--)road.lineTo(right[i][0],right[i][1]);road.close();p.setStyle(Paint.Style.FILL);p.setShader(new LinearGradient(0,top,0,bottom,topColor,bottomColor,Shader.TileMode.CLAMP));c.drawPath(road,p);p.setShader(null);
        drawRoadRibbon(c,p,outerL,left,Color.rgb(174,180,184));drawRoadRibbon(c,p,right,outerR,Color.rgb(166,173,178));
        p.setStyle(Paint.Style.STROKE);p.setStrokeWidth(2.2f);p.setColor(Color.rgb(143,151,157));Path le=new Path(),re=new Path();le.moveTo(outerL[0][0],outerL[0][1]);re.moveTo(outerR[0][0],outerR[0][1]);for(int i=1;i<n;i++){le.lineTo(outerL[i][0],outerL[i][1]);re.lineTo(outerR[i][0],outerR[i][1]);}c.drawPath(le,p);c.drawPath(re,p);
    }
    private void drawRoadRibbon(Canvas c,Paint p,float[][] a,float[][] b,int color){if(a.length<2)return;Path q=new Path();q.moveTo(a[0][0],a[0][1]);for(int i=1;i<a.length;i++)q.lineTo(a[i][0],a[i][1]);for(int i=b.length-1;i>=0;i--)q.lineTo(b[i][0],b[i][1]);q.close();p.setShader(null);p.setStyle(Paint.Style.FILL);p.setColor(color);c.drawPath(q,p);}
    private void drawRoadGrid3D(Canvas c,Paint p,JSONArray path,float cx,float top,float bottom){p.setShader(null);p.setStyle(Paint.Style.FILL);p.setColor(Color.rgb(194,200,204));for(float d:new float[]{8,16,28,44,64,88,114}){float center=pathCenterAt(path,d);float[] a=project(d,center+4.35f,cx,top,bottom),b=project(d,center-4.35f,cx,top,bottom);float near=1.0f-Math.min(1.0f,d/120.0f);drawPerspectiveSegment(c,p,a,b,0.8f+near*1.2f,0.8f+near*1.2f);}}
    private float pathAngleAt(JSONArray path,float distance,float cx,float top,float bottom){if(path==null||path.length()<2)return 0.0f;float d0=Math.max(1.0f,distance-2.0f),d1=Math.min(120.0f,distance+2.0f);float[] a=project(d0,pathCenterAt(path,d0),cx,top,bottom),b=project(d1,pathCenterAt(path,d1),cx,top,bottom);return (float)Math.toDegrees(Math.atan2(b[0]-a[0],-(b[1]-a[1])));}
'''
replace_between('    private void drawWorldLine(', '    private void drawPath(', new_world_helpers)

new_path = r'''    private void drawPath(Canvas c,Paint p,JSONArray pts,float cx,float top,float bottom,int color){
        float[][] leftPts=new float[pts.length()][],rightPts=new float[pts.length()][];int count=0;
        for(int i=0;i<pts.length();i++){JSONArray a=pts.optJSONArray(i);if(a==null)continue;float x=(float)a.optDouble(0),y=(float)a.optDouble(1);if(x<1.0f||x>120.0f)continue;float half=0.98f-0.20f*Math.min(1.0f,x/120.0f);leftPts[count]=project(x,y+half,cx,top,bottom);rightPts[count]=project(x,y-half,cx,top,bottom);count++;}
        if(count<2)return;Path fill=new Path();fill.moveTo(leftPts[0][0],leftPts[0][1]);for(int i=1;i<count;i++)fill.lineTo(leftPts[i][0],leftPts[i][1]);for(int i=count-1;i>=0;i--)fill.lineTo(rightPts[i][0],rightPts[i][1]);fill.close();int r=Color.red(color),g=Color.green(color),b=Color.blue(color);
        int save=c.save();c.translate(0,3.5f);p.setStyle(Paint.Style.FILL);p.setShader(null);p.setColor(Color.argb(65,20,40,58));c.drawPath(fill,p);c.restoreToCount(save);
        p.setShader(new LinearGradient(0,top,0,bottom,Color.argb(42,r,g,b),Color.argb(175,r,g,b),Shader.TileMode.CLAMP));c.drawPath(fill,p);p.setShader(null);
        p.setStyle(Paint.Style.STROKE);p.setStrokeCap(Paint.Cap.ROUND);p.setStrokeJoin(Paint.Join.ROUND);p.setStrokeWidth(4.8f);p.setColor(Color.rgb(Math.min(255,r+24),Math.min(255,g+28),Math.min(255,b+24)));Path left=new Path(),right=new Path();left.moveTo(leftPts[0][0],leftPts[0][1]);right.moveTo(rightPts[0][0],rightPts[0][1]);for(int i=1;i<count;i++){left.lineTo(leftPts[i][0],leftPts[i][1]);right.lineTo(rightPts[i][0],rightPts[i][1]);}c.drawPath(left,p);c.drawPath(right,p);
    }
'''
replace_between('    private void drawPath(', '    private void drawLeadVehicle(', new_path)

new_lead = r'''    private void drawLeadVehicle(Canvas c,Paint p,JSONObject lead,JSONArray path,float cx,float top,float bottom,boolean primary){
        float d=(float)lead.optDouble("d",0),y=(float)lead.optDouble("y",0);if(d<=0||d>120)return;float[] pt=project(d,y,cx,top,bottom);float perspective=66.0f/(1.0f+d/17.0f);float size=Math.max(primary?31.0f:27.0f,(primary?31.0f:27.0f)+perspective*(primary?0.43f:0.36f));float angle=pathAngleAt(path,d,cx,top,bottom);drawVehicle3D(c,p,otherCar,pt[0],pt[1]-8,size,angle,primary?248:210,primary?0.92f:0.78f);
    }
'''
replace_between('    private void drawLeadVehicle(', '    private void drawVehicle(', new_lead)

new_vehicle = r'''    private void drawVehicle(Canvas c,Paint p,Bitmap b,float cx,float cy,float width,float angle,int alpha){drawVehicle3D(c,p,b,cx,cy,width,angle,alpha,0.88f);}
    private void drawVehicle3D(Canvas c,Paint p,Bitmap b,float cx,float cy,float width,float angle,int alpha,float depthScale){
        if(b==null||b.isRecycled())return;float h=b.getHeight()*width/b.getWidth();int save=c.save();c.translate(cx,cy);c.rotate(angle);
        p.setShader(null);p.setStyle(Paint.Style.FILL);p.setColor(Color.argb(Math.min(105,alpha/2),18,22,25));RectF shadow=new RectF(-width*0.47f,Math.max(2.0f,-h*0.10f),width*0.47f,Math.max(8.0f,h*0.10f));c.drawOval(shadow,p);
        float depth=Math.max(2.0f,width*0.075f*depthScale);Path side=new Path();side.moveTo(-width/2.0f,-h+depth);side.lineTo(width/2.0f,-h+depth);side.lineTo(width/2.0f-depth*0.55f,-h);side.lineTo(-width/2.0f+depth*0.55f,-h);side.close();p.setColor(Color.argb(Math.min(120,alpha/2),80,88,94));c.drawPath(side,p);
        p.setAlpha(alpha);p.setFilterBitmap(true);c.drawBitmap(b,null,new RectF(-width/2.0f,-h,width/2.0f,0),p);p.setAlpha(255);c.restoreToCount(save);
    }
'''
replace_between('    private void drawVehicle(', '    private void drawBlinker(', new_vehicle)

path.write_text(text, encoding='utf-8')
print('Applied full S9 perspective 3D road/lane/path/vehicle/BSD patch')
