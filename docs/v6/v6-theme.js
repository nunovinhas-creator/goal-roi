// Tema antes da primeira pintura, para evitar o clarão no modo escuro.
try { const t = localStorage.getItem('goal-roi-v6-theme'); if (t === 'dark' || t === 'light') document.documentElement.dataset.theme = t; } catch (e) {}
