import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'
import { resolve } from 'path'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react(), tailwindcss()],
  build: {
    rollupOptions: {
      input: {
        // Panel admin/operativo (App.jsx) — el bundle grande, protegido
        // por clave, para el equipo.
        main: resolve(import.meta.dirname, 'index.html'),
        // Página de consulta para huéspedes (ConsultaView.jsx) — bundle
        // chico y aparte, sin login, pensada para el celular del
        // huésped. Ver vercel.json para la URL limpia /consulta.
        consulta: resolve(import.meta.dirname, 'consulta.html'),
      },
    },
  },
})
