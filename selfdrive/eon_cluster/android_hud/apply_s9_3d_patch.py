from pathlib import Path

path = Path('selfdrive/eon_cluster/android_hud/app/src/main/java/ai/comma/remotehud/HudService.java')
text = path.read_text(encoding='utf-8')

old_world = '''    private void drawWorldLine(Canvas c,Paint p,JSONArray pts,float cx,float top,float bottom,int color,float width,boolean dashed){p.setStyle(Paint.Style.STROKE);p.setStrokeWidth(width);p.setStrokeCap(Paint.Cap.ROUND);p.setColor(color);for(int i=0;i<pts.length()-1;i++){JSONArray a=pts.optJSONArray(i),b=pts.optJSONArray(i+1);if(a==null||b==null)continue;float x1=(float)a.optDouble(0),x2=(float)b.optDouble(0);if(dashed&&(((int)((x1+x2)*0.5/5))&1)!=0)continue;float[] pa=project(x1,(float)a.optDouble(1),cx,top,bottom),pb=project(x2,(float)b.optDouble(1),cx,top,bottom);c.drawLine(pa[0],pa[1],pb[0],pb[1],p);}}'''
new_world = '''    private void drawWorldLine(Canvas c,Paint p,JSONArray pts,float cx,float top,float bottom,int color,float width,boolean dashed){
        p.setStyle(Paint.Style.FILL);p.setShader(null);p.setColor(color);
        for(int i=0;i<pts.length()-1;i++){
            JSONArray a=pts.optJSONArray(i),b=pts.optJSONArray(i+1);if(a==null||b==null)continue;
            float x1=(float)a.optDouble(0),x2=(float)b.optDouble(0);if(dashed&&(((int)((x1+x2)*0.5f/5.0f))&1)!=0)continue;
            float[] pa=project(x1,(float)a.optDouble(1),cx,top,bottom),pb=project(x2,(float)b.optDouble(1),cx,top,bottom);
            float near=1.0f-Math.min(1.0f,Math.max(0.0f,(x1+x2)*0.5f/120.0f));
            float perspectiveWidth=Math.max(1.4f,width*(0.65f+1.85f*near));
            drawPerspectiveSegment(c,p,pa,pb,perspectiveWidth,perspectiveWidth*0.86f);
        }
    }
    private void drawPerspectiveSegment(Canvas c,Paint p,float[] a,float[] b,float wa,float wb){
        float dx=b[0]-a[0],dy=b[1]-a[1],len=(float)Math.hypot(dx,dy);if(len<0.5f)return;
        float nx=-dy/len,ny=dx/len,ha=wa*0.5f,hb=wb*0.5f;Path q=new Path();
        q.moveTo(a[0]+nx*ha,a[1]+ny*ha);q.lineTo(b[0]+nx*hb,b[1]+ny*hb);q.lineTo(b[0]-nx*hb,b[1]-ny*hb);q.lineTo(a[0]-nx*ha,a[1]-ny*ha);q.close();c.drawPath(q,p);
    }'''

old_path = '''    private void drawPath(Canvas c,Paint p,JSONArray pts,float cx,float top,float bottom,int color){Path left=new Path(),right=new Path();boolean first=true;for(int i=0;i<pts.length();i++){JSONArray a=pts.optJSONArray(i);if(a==null)continue;float x=(float)a.optDouble(0),y=(float)a.optDouble(1);float[] l=project(x,y+0.75f,cx,top,bottom),r=project(x,y-0.75f,cx,top,bottom);if(first){left.moveTo(l[0],l[1]);right.moveTo(r[0],r[1]);first=false;}else{left.lineTo(l[0],l[1]);right.lineTo(r[0],r[1]);}}p.setStyle(Paint.Style.STROKE);p.setStrokeWidth(4);p.setStrokeCap(Paint.Cap.ROUND);p.setColor(color);c.drawPath(left,p);c.drawPath(right,p);}'''
new_path = '''    private void drawPath(Canvas c,Paint p,JSONArray pts,float cx,float top,float bottom,int color){
        float[][] leftPts=new float[pts.length()][],rightPts=new float[pts.length()][];int count=0;
        for(int i=0;i<pts.length();i++){
            JSONArray a=pts.optJSONArray(i);if(a==null)continue;float x=(float)a.optDouble(0),y=(float)a.optDouble(1);if(x<1.0f||x>120.0f)continue;
            float half=0.92f-0.18f*Math.min(1.0f,x/120.0f);leftPts[count]=project(x,y+half,cx,top,bottom);rightPts[count]=project(x,y-half,cx,top,bottom);count++;
        }
        if(count<2)return;
        Path fill=new Path();fill.moveTo(leftPts[0][0],leftPts[0][1]);for(int i=1;i<count;i++)fill.lineTo(leftPts[i][0],leftPts[i][1]);for(int i=count-1;i>=0;i--)fill.lineTo(rightPts[i][0],rightPts[i][1]);fill.close();
        int r=Color.red(color),g=Color.green(color),b=Color.blue(color);p.setStyle(Paint.Style.FILL);p.setShader(new LinearGradient(0,top,0,bottom,Color.argb(35,r,g,b),Color.argb(150,r,g,b),Shader.TileMode.CLAMP));c.drawPath(fill,p);p.setShader(null);
        p.setStyle(Paint.Style.STROKE);p.setStrokeCap(Paint.Cap.ROUND);p.setStrokeJoin(Paint.Join.ROUND);p.setStrokeWidth(4.5f);p.setColor(Color.rgb(Math.min(255,r+18),Math.min(255,g+24),Math.min(255,b+24)));
        Path left=new Path(),right=new Path();left.moveTo(leftPts[0][0],leftPts[0][1]);right.moveTo(rightPts[0][0],rightPts[0][1]);for(int i=1;i<count;i++){left.lineTo(leftPts[i][0],leftPts[i][1]);right.lineTo(rightPts[i][0],rightPts[i][1]);}c.drawPath(left,p);c.drawPath(right,p);
    }'''

if old_world not in text:
    raise SystemExit('drawWorldLine source signature not found')
if old_path not in text:
    raise SystemExit('drawPath source signature not found')
text = text.replace(old_world, new_world).replace(old_path, new_path)
path.write_text(text, encoding='utf-8')
print('Applied S9 3D lane/path patch')
