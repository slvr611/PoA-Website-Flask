document.addEventListener('DOMContentLoaded', () => {
  const forms = document.querySelectorAll('form[data-prevent-enter-submit]');

  forms.forEach((form) => {
    form.addEventListener('keydown', (event) => {
      if (event.key !== 'Enter') return;
      if (event.target.tagName === 'TEXTAREA') return;

      event.preventDefault();
    });
  });
});
