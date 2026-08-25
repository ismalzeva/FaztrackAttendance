import type { Metadata, Viewport } from "next";
import type { ReactNode } from "react";
import "./styles.css";
export const metadata: Metadata={title:"Faztrack Attendance",description:"Attendance proof for field teams",manifest:"/manifest.webmanifest"};
export const viewport: Viewport={themeColor:"#102A43",width:"device-width",initialScale:1};
export default function RootLayout({children}:{children:ReactNode}){
  const apiBaseScript = {__html:`window.__API_BASE__="/api/v1";`} as const;
  return <html lang="id"><head><script dangerouslySetInnerHTML={apiBaseScript}/></head><body>{children}</body></html>;
}
