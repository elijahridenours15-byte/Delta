(function(global){
  const catalog = [
    {
      key: 'topographic',
      group: 'topo',
      label: 'OpenTopoMap HD',
      controlLabel: '&#x1F3D4; OpenTopoMap HD',
      summary: 'Classic contour map with SRTM shading and strong trail readability.',
      tileUrl: 'https://{s}.tile.opentopomap.org/{z}/{x}/{y}.png',
      maxZoom: 22,
      maxNativeZoom: 17,
      detectRetina: true,
      attribution: '&copy; <a href="https://opentopomap.org">OpenTopoMap</a> (<a href="https://creativecommons.org/licenses/by-sa/3.0/">CC-BY-SA</a>)'
    },
    {
      key: 'trainingTopo',
      group: 'topo',
      label: 'Land Nav Training HD',
      controlLabel: '&#x1F9ED; Land Nav Training HD',
      summary: 'Terrain base plus transparent reference overlay for land navigation drills and route study.',
      layers: [
        {
          tileUrl: 'https://services.arcgisonline.com/ArcGIS/rest/services/World_Terrain_Base/MapServer/tile/{z}/{y}/{x}',
          maxZoom: 22,
          maxNativeZoom: 13,
          detectRetina: true,
          attribution: 'Terrain &copy; Esri, USGS, NOAA'
        },
        {
          tileUrl: 'https://services.arcgisonline.com/ArcGIS/rest/services/Reference/World_Reference_Overlay/MapServer/tile/{z}/{y}/{x}',
          maxZoom: 22,
          maxNativeZoom: 13,
          opacity: 0.96,
          attribution: 'Reference &copy; Esri, Garmin, USGS, NPS'
        }
      ],
      attribution: 'Terrain &copy; Esri, USGS, NOAA | Reference &copy; Esri, Garmin, USGS, NPS'
    },
    {
      key: 'hillshadeReference',
      group: 'topo',
      label: 'World Hillshade Reference',
      controlLabel: '&#x26F0; World Hillshade Reference',
      summary: 'High-contrast relief shading with transparent labels for terrain-first route scanning.',
      layers: [
        {
          tileUrl: 'https://services.arcgisonline.com/ArcGIS/rest/services/Elevation/World_Hillshade/MapServer/tile/{z}/{y}/{x}',
          maxZoom: 22,
          maxNativeZoom: 23,
          attribution: 'Hillshade &copy; Esri, USGS, NGA, NASA'
        },
        {
          tileUrl: 'https://services.arcgisonline.com/ArcGIS/rest/services/Reference/World_Reference_Overlay/MapServer/tile/{z}/{y}/{x}',
          maxZoom: 22,
          maxNativeZoom: 13,
          opacity: 0.92,
          attribution: 'Reference &copy; Esri, Garmin, USGS, NPS'
        }
      ],
      attribution: 'Hillshade &copy; Esri, USGS, NGA, NASA | Reference &copy; Esri, Garmin, USGS, NPS'
    },
    {
      key: 'esriTopo',
      group: 'topo',
      label: 'World Topo HD',
      controlLabel: '&#x1F5FA; World Topo HD',
      summary: 'Global topo with shaded relief and high-detail urban coverage in supported regions.',
      tileUrl: 'https://server.arcgisonline.com/ArcGIS/rest/services/World_Topo_Map/MapServer/tile/{z}/{y}/{x}',
      maxZoom: 22,
      maxNativeZoom: 19,
      detectRetina: true,
      attribution: 'Tiles &copy; Esri'
    },
    {
      key: 'natgeo',
      group: 'topo',
      label: 'NatGeo Terrain',
      controlLabel: '&#x1F30E; NatGeo Terrain',
      summary: 'National Geographic cartography with shaded relief and strong North America detail.',
      tileUrl: 'https://server.arcgisonline.com/ArcGIS/rest/services/NatGeo_World_Map/MapServer/tile/{z}/{y}/{x}',
      maxZoom: 22,
      maxNativeZoom: 16,
      attribution: 'Tiles &copy; Esri &mdash; National Geographic, Esri, DeLorme, NAVTEQ, UNEP-WCMC, USGS, NASA, ESA, METI, NRCAN, GEBCO, NOAA, iPC'
    },
    {
      key: 'usgsTopo',
      group: 'topo',
      label: 'USGS Topo',
      controlLabel: '&#x1F5FB; USGS Topo',
      summary: 'USGS multi-scale topo reference map with contours, hydrography, land cover, and relief.',
      tileUrl: 'https://basemap.nationalmap.gov/arcgis/rest/services/USGSTopo/MapServer/tile/{z}/{y}/{x}',
      maxZoom: 22,
      maxNativeZoom: 16,
      attribution: 'Tiles &copy; USGS'
    },
    {
      key: 'usgs',
      group: 'topo',
      label: 'USGS Imagery + Topo',
      controlLabel: '&#x1F1FA;&#x1F1F8; USGS Imagery + Topo',
      summary: 'Public USGS orthoimagery fused with US Topo vectors and visible to 1:9,028 scale.',
      tileUrl: 'https://basemap.nationalmap.gov/arcgis/rest/services/USGSImageryTopo/MapServer/tile/{z}/{y}/{x}',
      maxZoom: 22,
      maxNativeZoom: 16,
      attribution: 'Tiles &copy; USGS'
    },
    {
      key: 'liveSatellite',
      group: 'tactical',
      label: 'Live Satellite Recon',
      controlLabel: '&#x1F4E1; Live Satellite Recon',
      summary: 'Animated day/night satellite loop over high-zoom Esri imagery, with GOES and Himawari motion where public live frames are available.',
      layers: [
        {
          tileUrl: 'https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}',
          maxZoom: 22,
          maxNativeZoom: 19,
          detectRetina: true,
          attribution: 'Base imagery &copy; Esri, Maxar, Earthstar Geographics, USDA FSA, GeoEye, CNES/Airbus DS, AeroGRID, IGN, and the GIS User Community'
        },
        {
          tileUrl: 'https://gibs.earthdata.nasa.gov/wmts/epsg3857/best/VIIRS_SNPP_CorrectedReflectance_TrueColor/default/GoogleMapsCompatible_Level9/{z}/{y}/{x}.jpg',
          maxZoom: 22,
          maxNativeZoom: 9,
          opacity: 0.58,
          attribution: 'Near-real-time overlay &copy; NASA GIBS, LANCE, and VIIRS'
        }
      ],
      attribution: 'Base imagery &copy; Esri, Maxar, Earthstar Geographics, USDA FSA, GeoEye, CNES/Airbus DS, AeroGRID, IGN, and the GIS User Community | Near-real-time overlay &copy; NASA GIBS, LANCE, and VIIRS'
    },
    {
      key: 'satellite',
      group: 'tactical',
      label: 'Satellite Recon',
      controlLabel: '&#x1F6F0; Satellite Recon',
      summary: 'Global imagery composite for reconnaissance context and rapid terrain verification.',
      tileUrl: 'https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}',
      maxZoom: 22,
      maxNativeZoom: 19,
      detectRetina: true,
      attribution: '&copy; Esri &mdash; Esri, i-cubed, USDA, USGS, AEX, GeoEye, Getmapping, Aerogrid, IGN, IGP, UPR-EGP, and the GIS User Community'
    },
    {
      key: 'street',
      group: 'tactical',
      label: 'Street Map',
      controlLabel: '&#x1F30D; Street Map',
      summary: 'Standard street and place-name basemap for urban routing and address search.',
      tileUrl: 'https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png',
      maxZoom: 22,
      maxNativeZoom: 19,
      detectRetina: true,
      attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>'
    },
    {
      key: 'voyager',
      group: 'tactical',
      label: 'CARTO Voyager',
      controlLabel: '&#x1F30C; CARTO Voyager',
      summary: 'Clean tactical reference map with broad world coverage and readable labels.',
      tileUrl: 'https://{s}.basemaps.cartocdn.com/rastertiles/voyager/{z}/{x}/{y}{r}.png',
      maxZoom: 22,
      maxNativeZoom: 20,
      detectRetina: true,
      attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OSM</a> &copy; <a href="https://carto.com/">CARTO</a>'
    },
    {
      key: 'dark',
      group: 'tactical',
      label: 'Dark Tactical',
      controlLabel: '&#x1F311; Dark Tactical',
      summary: 'Low-light tactical basemap for night-mode overlays and high-contrast scans.',
      tileUrl: 'https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png',
      maxZoom: 22,
      maxNativeZoom: 20,
      detectRetina: true,
      attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OSM</a> &copy; <a href="https://carto.com/">CARTO</a>'
    },
    {
      key: 'cyclosm',
      group: 'tactical',
      label: 'CyclOSM Trails',
      controlLabel: '&#x1F6B2; CyclOSM Trails',
      summary: 'Trail and cycle emphasis for path scouting in parks, urban routes, and mixed terrain.',
      tileUrl: 'https://{s}.tile-cyclosm.openstreetmap.fr/cyclosm/{z}/{x}/{y}.png',
      maxZoom: 22,
      maxNativeZoom: 20,
      detectRetina: true,
      attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors &copy; <a href="https://www.cyclosm.org/">CyclOSM</a>'
    },
    {
      key: 'hot',
      group: 'tactical',
      label: 'OSM Humanitarian',
      controlLabel: '&#x1F6A8; OSM Humanitarian',
      summary: 'Humanitarian-focused roads and settlements for response planning and logistics.',
      tileUrl: 'https://{s}.tile.openstreetmap.fr/hot/{z}/{x}/{y}.png',
      maxZoom: 22,
      maxNativeZoom: 20,
      detectRetina: true,
      attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors, Tiles style by <a href="https://www.hotosm.org/" target="_blank">Humanitarian OpenStreetMap Team</a> hosted by <a href="https://openstreetmap.fr/" target="_blank">OpenStreetMap France</a>'
    },
    {
      key: 'esriStreet',
      group: 'tactical',
      label: 'ESRI Street',
      controlLabel: '&#x1F6E3; ESRI Street',
      summary: 'Street-focused Esri reference map for urban routing and built-up areas.',
      tileUrl: 'https://server.arcgisonline.com/ArcGIS/rest/services/World_Street_Map/MapServer/tile/{z}/{y}/{x}',
      maxZoom: 22,
      maxNativeZoom: 19,
      detectRetina: true,
      attribution: 'Tiles &copy; Esri'
    }
  ];

  global.DELTA_MAP_LAYER_CATALOG = Object.freeze(catalog);
})(window);