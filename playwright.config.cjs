const { defineConfig } = require('@playwright/test');
const path = require('path');
module.exports = defineConfig({
  testDir:'./tests', testMatch:'*.spec.js', workers:1, timeout:30000,
  reporter:'list',
  use:{baseURL:'http://127.0.0.1:8770', viewport:{width:1440,height:960},
    launchOptions:{executablePath:'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe'},
    trace:'retain-on-failure'},
  webServer:{command:'".\\pywechat-main\\.venv\\Scripts\\python.exe" -m wechat_panel --demo --port 8770 --no-browser',
    url:'http://127.0.0.1:8770/api/status',reuseExistingServer:false,timeout:30000,
    env:{WECHAT_PANEL_DATA_DIR:path.resolve('.panel-demo-data',`browser-${process.pid}`)}},
});
