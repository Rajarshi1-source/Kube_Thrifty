/**
 * Tailwind CSS v4 is a PostCSS plugin and nothing else.
 *
 * There is deliberately no `tailwind.config.js` in this project: v4 is CSS-first, and the design
 * tokens live in `@theme` inside `app/globals.css`. Adding a JS config back would split the token
 * definitions across two files that can disagree.
 */
const config = {
  plugins: {
    '@tailwindcss/postcss': {},
  },
};

export default config;
