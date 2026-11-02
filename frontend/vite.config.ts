import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import {pwaBuild} from './pwa-build.mjs'
export default defineConfig({plugins:[react(),pwaBuild()],server:{host:'0.0.0.0',port:5173,proxy:{'/api':{target:'http://localhost:8000',changeOrigin:false,ws:true}}}})
