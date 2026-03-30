import React, { useState, useRef, useEffect } from "react";
import { Upload, Play, Pause, RefreshCw, Video as VideoIcon } from "lucide-react";
import { motion } from "motion/react";

interface VideoSource {
  url: string;
  file: File | null;
}

export default function App() {
  const [topVideo, setTopVideo] = useState<VideoSource | null>(null);
  const [bottomVideo, setBottomVideo] = useState<VideoSource | null>(null);
  const [isPlaying, setIsPlaying] = useState(false);
  const [progress, setProgress] = useState(0);

  const topRef = useRef<HTMLVideoElement>(null);
  const topBgRef = useRef<HTMLVideoElement>(null);
  const bottomRef = useRef<HTMLVideoElement>(null);
  const bottomBgRef = useRef<HTMLVideoElement>(null);

  const handleFileUpload = (e: React.ChangeEvent<HTMLInputElement>, position: 'top' | 'bottom') => {
    const file = e.target.files?.[0];
    if (file) {
      const url = URL.createObjectURL(file);
      if (position === 'top') {
        setTopVideo({ url, file });
      } else {
        setBottomVideo({ url, file });
      }
    }
  };

  const togglePlay = () => {
    const videos = [topRef.current, topBgRef.current, bottomRef.current, bottomBgRef.current];
    if (isPlaying) {
      videos.forEach(v => v?.pause());
    } else {
      videos.forEach(v => v?.play());
    }
    setIsPlaying(!isPlaying);
  };

  useEffect(() => {
    const interval = setInterval(() => {
      if (topRef.current && isPlaying) {
        const p = (topRef.current.currentTime / topRef.current.duration) * 100;
        setProgress(p);
      }
    }, 100);
    return () => clearInterval(interval);
  }, [isPlaying]);

  // Sync videos
  useEffect(() => {
    if (topRef.current) {
      topRef.current.onplay = () => {
        [topBgRef.current, bottomRef.current, bottomBgRef.current].forEach(v => v?.play());
        setIsPlaying(true);
      };
      topRef.current.onpause = () => {
        [topBgRef.current, bottomRef.current, bottomBgRef.current].forEach(v => v?.pause());
        setIsPlaying(false);
      };
      topRef.current.onseeked = () => {
        const time = topRef.current?.currentTime || 0;
        [topBgRef.current, bottomRef.current, bottomBgRef.current].forEach(v => {
          if (v) v.currentTime = time;
        });
      };
    }
  }, [topVideo, bottomVideo]);

  return (
    <div className="min-h-screen bg-[#0a0a0a] text-white font-sans selection:bg-orange-500/30">
      <div className="max-w-6xl mx-auto px-4 py-12 flex flex-col lg:flex-row gap-12 items-start justify-center">
        
        {/* Controls Section */}
        <div className="w-full lg:w-1/2 space-y-8">
          <header className="space-y-2">
            <h1 className="text-4xl font-bold tracking-tight bg-gradient-to-r from-orange-400 to-rose-400 bg-clip-text text-transparent">
              Vertical Splitter
            </h1>
            <p className="text-zinc-400 text-lg">
              Создавай контент для TikTok и Reels в формате 9:16 с эффектом размытого фона.
            </p>
          </header>

          <div className="grid grid-cols-1 gap-6">
            {/* Top Video Upload */}
            <div className="p-6 rounded-2xl bg-zinc-900/50 border border-zinc-800 hover:border-orange-500/50 transition-colors group">
              <label className="flex flex-col gap-4 cursor-pointer">
                <div className="flex items-center gap-3">
                  <div className="p-2 rounded-lg bg-orange-500/10 text-orange-400 group-hover:scale-110 transition-transform">
                    <Upload size={20} />
                  </div>
                  <span className="font-semibold">Верхнее видео (Юзер)</span>
                </div>
                <input type="file" accept="video/*" className="hidden" onChange={(e) => handleFileUpload(e, 'top')} />
                <div className="text-sm text-zinc-500">
                  {topVideo ? topVideo.file?.name : "Нажмите, чтобы загрузить видео"}
                </div>
              </label>
            </div>

            {/* Bottom Video Upload */}
            <div className="p-6 rounded-2xl bg-zinc-900/50 border border-zinc-800 hover:border-rose-500/50 transition-colors group">
              <label className="flex flex-col gap-4 cursor-pointer">
                <div className="flex items-center gap-3">
                  <div className="p-2 rounded-lg bg-rose-500/10 text-rose-400 group-hover:scale-110 transition-transform">
                    <VideoIcon size={20} />
                  </div>
                  <span className="font-semibold">Нижнее видео (Наше)</span>
                </div>
                <input type="file" accept="video/*" className="hidden" onChange={(e) => handleFileUpload(e, 'bottom')} />
                <div className="text-sm text-zinc-500">
                  {bottomVideo ? bottomVideo.file?.name : "Нажмите, чтобы загрузить видео"}
                </div>
              </label>
            </div>
          </div>

          {/* Playback Controls */}
          {(topVideo || bottomVideo) && (
            <div className="p-6 rounded-2xl bg-zinc-900/80 border border-zinc-800 space-y-6">
              <div className="flex items-center justify-between">
                <button 
                  onClick={togglePlay}
                  className="flex items-center gap-2 px-6 py-3 rounded-full bg-white text-black font-bold hover:bg-zinc-200 transition-colors active:scale-95"
                >
                  {isPlaying ? <Pause size={20} fill="currentColor" /> : <Play size={20} fill="currentColor" />}
                  {isPlaying ? "Пауза" : "Играть"}
                </button>
                
                <button 
                  onClick={() => window.location.reload()}
                  className="p-3 rounded-full bg-zinc-800 text-zinc-400 hover:text-white transition-colors"
                >
                  <RefreshCw size={20} />
                </button>
              </div>

              <div className="space-y-2">
                <div className="h-1.5 w-full bg-zinc-800 rounded-full overflow-hidden">
                  <motion.div 
                    className="h-full bg-orange-500"
                    initial={{ width: 0 }}
                    animate={{ width: `${progress}%` }}
                    transition={{ duration: 0.1 }}
                  />
                </div>
                <div className="flex justify-between text-xs text-zinc-500 font-mono">
                  <span>0:00</span>
                  <span>PREVIEW</span>
                </div>
              </div>
            </div>
          )}
        </div>

        {/* Preview Section */}
        <div className="w-full lg:w-auto flex justify-center">
          <div className="relative w-[360px] aspect-[9/16] bg-black rounded-[40px] border-[8px] border-zinc-800 overflow-hidden shadow-2xl shadow-orange-500/10">
            
            {/* Top Half */}
            <div className="absolute top-0 left-0 w-full h-1/2 border-b border-zinc-800/50 overflow-hidden bg-zinc-900">
              {topVideo ? (
                <div className="relative w-full h-full">
                  {/* Blurred Background */}
                  <video 
                    ref={topBgRef}
                    src={topVideo.url}
                    className="absolute inset-0 w-full h-full object-cover blur-2xl opacity-50 scale-110"
                    muted
                    loop
                    playsInline
                  />
                  {/* Main Video */}
                  <video 
                    ref={topRef}
                    src={topVideo.url}
                    className="relative w-full h-full object-contain z-10"
                    muted={false}
                    loop
                    playsInline
                  />
                </div>
              ) : (
                <div className="w-full h-full flex flex-col items-center justify-center text-zinc-600 gap-2">
                  <Upload size={32} />
                  <span className="text-xs uppercase tracking-widest font-bold">Top Video</span>
                </div>
              )}
            </div>

            {/* Bottom Half */}
            <div className="absolute bottom-0 left-0 w-full h-1/2 overflow-hidden bg-zinc-900">
              {bottomVideo ? (
                <div className="relative w-full h-full">
                  {/* Blurred Background */}
                  <video 
                    ref={bottomBgRef}
                    src={bottomVideo.url}
                    className="absolute inset-0 w-full h-full object-cover blur-2xl opacity-50 scale-110"
                    muted
                    loop
                    playsInline
                  />
                  {/* Main Video */}
                  <video 
                    ref={bottomRef}
                    src={bottomVideo.url}
                    className="relative w-full h-full object-contain z-10"
                    muted
                    loop
                    playsInline
                  />
                </div>
              ) : (
                <div className="w-full h-full flex flex-col items-center justify-center text-zinc-600 gap-2">
                  <VideoIcon size={32} />
                  <span className="text-xs uppercase tracking-widest font-bold">Bottom Video</span>
                </div>
              )}
            </div>

            {/* Overlay UI */}
            <div className="absolute inset-0 pointer-events-none z-20">
              <div className="absolute top-8 left-1/2 -translate-x-1/2 w-12 h-1 bg-zinc-800 rounded-full opacity-50" />
              <div className="absolute bottom-4 left-1/2 -translate-x-1/2 w-24 h-1 bg-zinc-800 rounded-full opacity-50" />
            </div>
          </div>
        </div>

      </div>
    </div>
  );
}
