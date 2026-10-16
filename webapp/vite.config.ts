import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

export default defineConfig({
  base: '/',
  plugins: [react()],
  server: { proxy: { '/api': 'http://127.0.0.1:8000' } },
  build: {
    rolldownOptions: {
      output: {
        codeSplitting: {
          groups: [
            { name: 'react-vendor', test: /node_modules[\\/]react(?:-dom)?[\\/]/, priority: 20 },
            {
              name: 'three-vendor',
              test: /node_modules[\\/](?:three|@react-three)[\\/]/,
              maxSize: 450_000,
              priority: 10,
            },
          ],
        },
      },
    },
  },
})
