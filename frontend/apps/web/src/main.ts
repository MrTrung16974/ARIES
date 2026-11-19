import { bootstrapApplication } from '@angular/platform-browser';
import { loadAppConfig } from '@aiagent/core';
import { App } from './app/app';
import { buildAppConfig } from './app/app.config';

loadAppConfig()
  .then((config) => bootstrapApplication(App, buildAppConfig(config)))
  .catch((err: unknown) => {
    console.error(err);
    document.body.textContent =
      'Không khởi động được ứng dụng. Vui lòng thử lại sau.';
  });
