/* Tailwind 3.4.17. CRM del ejecutivo (index), panel y admin: modo oscuro por clase y colores "brand" del CRM
   (los mismos que index.html declaraba en su tailwind.config en línea). */
module.exports = {
  darkMode: 'class',
  content: [
    './frontend/index.html',
    './frontend/panel.html',
    './frontend/admin.html',
  ],
  theme: {
    extend: {
      colors: {
        brand: { 50: '#f0f9ff', 100: '#e0f2fe', 500: '#0ea5e9', 600: '#0284c7', 700: '#0369a1', 900: '#0c4a6e' },
      },
    },
  },
  plugins: [],
};
