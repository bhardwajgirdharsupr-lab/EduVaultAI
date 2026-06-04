(() => {
  const reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  const nav = document.querySelector(".public-nav, .app-topbar");

  if (nav) {
    const updateNav = () => nav.classList.toggle("is-scrolled", window.scrollY > 12);
    updateNav();
    window.addEventListener("scroll", updateNav, { passive: true });
  }

  if (reduceMotion) return;

  document.documentElement.classList.add("motion-ready");

  const revealTargets = document.querySelectorAll(
    [
      ".hero-copy",
      ".hero-search",
      ".feature-pill",
      ".auth-card",
      ".detail-card",
      ".panel",
      ".plan-card",
      ".course-card",
      ".certificate-card",
      ".resource-card",
      ".stat-card",
      ".timeline-item",
      ".integration-grid > div",
    ].join(",")
  );

  revealTargets.forEach((element, index) => {
    element.classList.add("reveal-on-scroll");
    element.style.setProperty("--reveal-delay", `${Math.min(index % 8, 7) * 45}ms`);
  });

  const observer = new IntersectionObserver(
    (entries) => {
      entries.forEach((entry) => {
        if (!entry.isIntersecting) return;
        entry.target.classList.add("is-visible");
        observer.unobserve(entry.target);
      });
    },
    { rootMargin: "0px 0px -8% 0px", threshold: 0.12 }
  );

  revealTargets.forEach((element) => observer.observe(element));
})();
