/**
 * Folder Gallery Card for Home Assistant
 * 
 * A custom Lovelace card that displays images from a folder
 * and triggers actions when clicked.
 * 
 * Installation:
 * Bundled with the SamsungTV Smart integration and auto-registered as a
 * Lovelace resource at /api/samsungtv_smart/folder-gallery-card.js — no manual
 * copy or resource entry is needed. (Restart Home Assistant once if the card
 * does not appear.)
 *
 * Usage Example:
 * type: custom:folder-gallery-card
 * title: My Art Gallery
 * folder: /local/frame_art/{entry_id}/personal
 * columns: 4
 * image_height: 150px
 * action:
 *   service: media_player.play_media
 *   target:
 *     entity_id: media_player.samsung_frame_tv
 *   data:
 *     media_content_type: image
 *     media_content_id: "{{image_path}}"
 */

// --- i18n --------------------------------------------------------------------
// Custom cards don't pick up Home Assistant's translation files automatically,
// so the card ships its own small dictionary. Strings support {name}-style
// placeholders replaced by t(). Languages: en (fallback), fr, es, it, pt-BR, hu.
const TRANSLATIONS = {
  en: {
    err_define_one: 'You need to define at least one of: folder, sensor, folder_sensor, or image_list',
    err_tv_required: 'Folder Gallery: a Frame TV entity is required for the configured tap / double-tap / long-press action',
    lb_select: 'Select',
    lb_unfavourite: '★ Unfavourite',
    lb_upload: '⬆ Upload',
    lb_delete: '🗑 Delete',
    lb_close: 'Close',
    frame_chooser_title: 'Upload to which Frame?',
    no_images: 'No images found',
    configure_sensor: 'Configure a sensor or image_list',
    folder_label: 'Folder',
    folder_all: 'All',
    images_count: '{n} images',
    removed_fav: 'Removed from favourites',
    artwork_deleted: 'Artwork deleted',
    all_frames: '⬆ All Frames ({n})',
    uploading_all: 'Uploading to all Frames ({n})…',
    upload_to: 'Upload → {name}',
    cancel: 'Cancel',
    action_executed: 'Action executed: {name}',
    error: 'Error: {msg}',
    g_nothing: 'Nothing',
    g_lightbox: 'Open preview (lightbox)',
    g_upload: 'Upload to TV',
    g_select: 'Display on TV',
    g_delete: 'Delete',
    g_unfavourite: 'Unfavourite',
    ed_title: 'Title',
    ed_sensor: 'Sensor (provides the image list)',
    ed_sensor_hint: 'Required — a <code>platform: folder</code> sensor (e.g. <code>sensor.&lt;tv&gt;_store</code>). This is what lists the images.',
    ed_folder: 'Folder Path (optional)',
    ed_folder_ph: 'auto-detected from the sensor',
    ed_folder_hint_auto: 'Auto-detected from the sensor — leave empty unless your files are outside <code>/config/www/</code>.',
    ed_folder_hint_manual: 'Only the base URL for thumbnails — it does <b>not</b> list images. On its own it shows nothing; set a sensor above.',
    ed_columns: 'Columns',
    ed_image_height: 'Image Height',
    ed_gallery_type: 'Gallery type (lightbox buttons)',
    gt_auto: 'Auto (detect per image)',
    gt_personal: 'Personal — Select + Delete',
    gt_favorites: 'Favorites — Select + Unfavourite',
    gt_upload: 'Upload — Upload',
    ed_gallery_type_hint: 'Forces which action buttons appear in the fullscreen preview for the whole gallery.',
    ed_thumbnails: 'Thumbnails',
    ed_server_thumbs: 'Server-side resized thumbnails',
    ed_server_thumbs_hint: 'Recommended for folders of full-size originals — sends small thumbnails to the browser instead of the multi-MB files. The full image is still used on click.',
    ed_thumb_width: 'Thumbnail width (px)',
    ed_actions: 'Actions',
    ed_tv_entity: 'Frame TV entity (for "Display on TV")',
    ed_single_tap: 'Single tap',
    ed_double_tap: 'Double tap',
    ed_long_press: 'Long press',
    ed_actions_hint: 'The actions here follow the <b>Gallery type</b> above, and need the Frame TV entity.',
  },
  fr: {
    err_define_one: 'Vous devez définir au moins l\'un des éléments suivants : folder, sensor, folder_sensor ou image_list',
    err_tv_required: 'Folder Gallery : une entité Frame TV est requise pour l\'action configurée (appui simple / double / long)',
    lb_select: 'Sélectionner',
    lb_unfavourite: '★ Retirer des favoris',
    lb_upload: '⬆ Envoyer',
    lb_delete: '🗑 Supprimer',
    lb_close: 'Fermer',
    frame_chooser_title: 'Envoyer vers quelle Frame ?',
    no_images: 'Aucune image trouvée',
    configure_sensor: 'Configurez un capteur ou image_list',
    folder_label: 'Dossier',
    folder_all: 'Tous',
    images_count: '{n} images',
    removed_fav: 'Retiré des favoris',
    artwork_deleted: 'Œuvre supprimée',
    all_frames: '⬆ Toutes les Frames ({n})',
    uploading_all: 'Envoi vers toutes les Frames ({n})…',
    upload_to: 'Envoyer → {name}',
    cancel: 'Annuler',
    action_executed: 'Action exécutée : {name}',
    error: 'Erreur : {msg}',
    g_nothing: 'Aucune',
    g_lightbox: 'Ouvrir l\'aperçu (lightbox)',
    g_upload: 'Envoyer sur la TV',
    g_select: 'Afficher sur la TV',
    g_delete: 'Supprimer',
    g_unfavourite: 'Retirer des favoris',
    ed_title: 'Titre',
    ed_sensor: 'Capteur (fournit la liste des images)',
    ed_sensor_hint: 'Requis — un capteur <code>platform: folder</code> (ex. <code>sensor.&lt;tv&gt;_store</code>). C\'est lui qui liste les images.',
    ed_folder: 'Chemin du dossier (facultatif)',
    ed_folder_ph: 'détecté automatiquement depuis le capteur',
    ed_folder_hint_auto: 'Détecté automatiquement depuis le capteur — laissez vide sauf si vos fichiers sont hors de <code>/config/www/</code>.',
    ed_folder_hint_manual: 'Seulement l\'URL de base des miniatures — il ne <b>liste pas</b> les images. Seul, il n\'affiche rien ; définissez un capteur ci-dessus.',
    ed_columns: 'Colonnes',
    ed_image_height: 'Hauteur des images',
    ed_gallery_type: 'Type de galerie (boutons du lightbox)',
    gt_auto: 'Auto (détecter par image)',
    gt_personal: 'Personnelle — Sélectionner + Supprimer',
    gt_favorites: 'Favoris — Sélectionner + Retirer des favoris',
    gt_upload: 'Envoi — Envoyer',
    ed_gallery_type_hint: 'Force les boutons d\'action affichés dans l\'aperçu plein écran pour toute la galerie.',
    ed_thumbnails: 'Miniatures',
    ed_server_thumbs: 'Miniatures redimensionnées côté serveur',
    ed_server_thumbs_hint: 'Recommandé pour les dossiers d\'originaux pleine taille — envoie de petites miniatures au navigateur au lieu des fichiers de plusieurs Mo. L\'image complète est toujours utilisée au clic.',
    ed_thumb_width: 'Largeur des miniatures (px)',
    ed_actions: 'Actions',
    ed_tv_entity: 'Entité Frame TV (pour « Afficher sur la TV »)',
    ed_single_tap: 'Appui simple',
    ed_double_tap: 'Double appui',
    ed_long_press: 'Appui long',
    ed_actions_hint: 'Les actions ici suivent le <b>Type de galerie</b> ci-dessus et nécessitent l\'entité Frame TV.',
  },
  es: {
    err_define_one: 'Debes definir al menos uno de: folder, sensor, folder_sensor o image_list',
    err_tv_required: 'Folder Gallery: se requiere una entidad Frame TV para la acción configurada (toque / doble toque / pulsación larga)',
    lb_select: 'Seleccionar',
    lb_unfavourite: '★ Quitar de favoritos',
    lb_upload: '⬆ Subir',
    lb_delete: '🗑 Eliminar',
    lb_close: 'Cerrar',
    frame_chooser_title: '¿A qué Frame subir?',
    no_images: 'No se encontraron imágenes',
    configure_sensor: 'Configura un sensor o image_list',
    folder_label: 'Carpeta',
    folder_all: 'Todas',
    images_count: '{n} imágenes',
    removed_fav: 'Eliminado de favoritos',
    artwork_deleted: 'Obra eliminada',
    all_frames: '⬆ Todas las Frames ({n})',
    uploading_all: 'Subiendo a todas las Frames ({n})…',
    upload_to: 'Subir → {name}',
    cancel: 'Cancelar',
    action_executed: 'Acción ejecutada: {name}',
    error: 'Error: {msg}',
    g_nothing: 'Nada',
    g_lightbox: 'Abrir vista previa (lightbox)',
    g_upload: 'Subir a la TV',
    g_select: 'Mostrar en la TV',
    g_delete: 'Eliminar',
    g_unfavourite: 'Quitar de favoritos',
    ed_title: 'Título',
    ed_sensor: 'Sensor (proporciona la lista de imágenes)',
    ed_sensor_hint: 'Obligatorio — un sensor <code>platform: folder</code> (p. ej. <code>sensor.&lt;tv&gt;_store</code>). Es lo que lista las imágenes.',
    ed_folder: 'Ruta de carpeta (opcional)',
    ed_folder_ph: 'detectado automáticamente desde el sensor',
    ed_folder_hint_auto: 'Detectado automáticamente desde el sensor — déjalo vacío salvo que tus archivos estén fuera de <code>/config/www/</code>.',
    ed_folder_hint_manual: 'Solo la URL base de las miniaturas — <b>no</b> lista imágenes. Por sí solo no muestra nada; define un sensor arriba.',
    ed_columns: 'Columnas',
    ed_image_height: 'Altura de imagen',
    ed_gallery_type: 'Tipo de galería (botones del lightbox)',
    gt_auto: 'Auto (detectar por imagen)',
    gt_personal: 'Personal — Seleccionar + Eliminar',
    gt_favorites: 'Favoritos — Seleccionar + Quitar de favoritos',
    gt_upload: 'Subida — Subir',
    ed_gallery_type_hint: 'Fuerza qué botones de acción aparecen en la vista previa a pantalla completa para toda la galería.',
    ed_thumbnails: 'Miniaturas',
    ed_server_thumbs: 'Miniaturas redimensionadas en el servidor',
    ed_server_thumbs_hint: 'Recomendado para carpetas de originales a tamaño completo — envía miniaturas pequeñas al navegador en lugar de los archivos de varios MB. La imagen completa se sigue usando al hacer clic.',
    ed_thumb_width: 'Ancho de miniatura (px)',
    ed_actions: 'Acciones',
    ed_tv_entity: 'Entidad Frame TV (para «Mostrar en la TV»)',
    ed_single_tap: 'Toque simple',
    ed_double_tap: 'Doble toque',
    ed_long_press: 'Pulsación larga',
    ed_actions_hint: 'Las acciones aquí siguen el <b>Tipo de galería</b> de arriba y necesitan la entidad Frame TV.',
  },
  it: {
    err_define_one: 'Devi definire almeno uno tra: folder, sensor, folder_sensor o image_list',
    err_tv_required: 'Folder Gallery: è richiesta un\'entità Frame TV per l\'azione configurata (tocco / doppio tocco / pressione lunga)',
    lb_select: 'Seleziona',
    lb_unfavourite: '★ Rimuovi dai preferiti',
    lb_upload: '⬆ Carica',
    lb_delete: '🗑 Elimina',
    lb_close: 'Chiudi',
    frame_chooser_title: 'Su quale Frame caricare?',
    no_images: 'Nessuna immagine trovata',
    configure_sensor: 'Configura un sensore o image_list',
    folder_label: 'Cartella',
    folder_all: 'Tutte',
    images_count: '{n} immagini',
    removed_fav: 'Rimosso dai preferiti',
    artwork_deleted: 'Opera eliminata',
    all_frames: '⬆ Tutte le Frame ({n})',
    uploading_all: 'Caricamento su tutte le Frame ({n})…',
    upload_to: 'Carica → {name}',
    cancel: 'Annulla',
    action_executed: 'Azione eseguita: {name}',
    error: 'Errore: {msg}',
    g_nothing: 'Niente',
    g_lightbox: 'Apri anteprima (lightbox)',
    g_upload: 'Carica sulla TV',
    g_select: 'Mostra sulla TV',
    g_delete: 'Elimina',
    g_unfavourite: 'Rimuovi dai preferiti',
    ed_title: 'Titolo',
    ed_sensor: 'Sensore (fornisce l\'elenco delle immagini)',
    ed_sensor_hint: 'Obbligatorio — un sensore <code>platform: folder</code> (es. <code>sensor.&lt;tv&gt;_store</code>). È ciò che elenca le immagini.',
    ed_folder: 'Percorso cartella (facoltativo)',
    ed_folder_ph: 'rilevato automaticamente dal sensore',
    ed_folder_hint_auto: 'Rilevato automaticamente dal sensore — lascia vuoto a meno che i file non siano fuori da <code>/config/www/</code>.',
    ed_folder_hint_manual: 'Solo l\'URL di base per le miniature — <b>non</b> elenca le immagini. Da solo non mostra nulla; imposta un sensore sopra.',
    ed_columns: 'Colonne',
    ed_image_height: 'Altezza immagine',
    ed_gallery_type: 'Tipo di galleria (pulsanti lightbox)',
    gt_auto: 'Auto (rileva per immagine)',
    gt_personal: 'Personale — Seleziona + Elimina',
    gt_favorites: 'Preferiti — Seleziona + Rimuovi dai preferiti',
    gt_upload: 'Caricamento — Carica',
    ed_gallery_type_hint: 'Forza quali pulsanti azione appaiono nell\'anteprima a schermo intero per l\'intera galleria.',
    ed_thumbnails: 'Miniature',
    ed_server_thumbs: 'Miniature ridimensionate lato server',
    ed_server_thumbs_hint: 'Consigliato per cartelle di originali a piena risoluzione — invia piccole miniature al browser invece dei file da diversi MB. L\'immagine completa è comunque usata al clic.',
    ed_thumb_width: 'Larghezza miniatura (px)',
    ed_actions: 'Azioni',
    ed_tv_entity: 'Entità Frame TV (per «Mostra sulla TV»)',
    ed_single_tap: 'Tocco singolo',
    ed_double_tap: 'Doppio tocco',
    ed_long_press: 'Pressione lunga',
    ed_actions_hint: 'Le azioni qui seguono il <b>Tipo di galleria</b> sopra e richiedono l\'entità Frame TV.',
  },
  'pt-BR': {
    err_define_one: 'Você precisa definir pelo menos um: folder, sensor, folder_sensor ou image_list',
    err_tv_required: 'Folder Gallery: uma entidade Frame TV é necessária para a ação configurada (toque / toque duplo / pressão longa)',
    lb_select: 'Selecionar',
    lb_unfavourite: '★ Remover dos favoritos',
    lb_upload: '⬆ Enviar',
    lb_delete: '🗑 Excluir',
    lb_close: 'Fechar',
    frame_chooser_title: 'Enviar para qual Frame?',
    no_images: 'Nenhuma imagem encontrada',
    configure_sensor: 'Configure um sensor ou image_list',
    folder_label: 'Pasta',
    folder_all: 'Todas',
    images_count: '{n} imagens',
    removed_fav: 'Removido dos favoritos',
    artwork_deleted: 'Obra excluída',
    all_frames: '⬆ Todas as Frames ({n})',
    uploading_all: 'Enviando para todas as Frames ({n})…',
    upload_to: 'Enviar → {name}',
    cancel: 'Cancelar',
    action_executed: 'Ação executada: {name}',
    error: 'Erro: {msg}',
    g_nothing: 'Nada',
    g_lightbox: 'Abrir prévia (lightbox)',
    g_upload: 'Enviar para a TV',
    g_select: 'Exibir na TV',
    g_delete: 'Excluir',
    g_unfavourite: 'Remover dos favoritos',
    ed_title: 'Título',
    ed_sensor: 'Sensor (fornece a lista de imagens)',
    ed_sensor_hint: 'Obrigatório — um sensor <code>platform: folder</code> (ex. <code>sensor.&lt;tv&gt;_store</code>). É o que lista as imagens.',
    ed_folder: 'Caminho da pasta (opcional)',
    ed_folder_ph: 'detectado automaticamente pelo sensor',
    ed_folder_hint_auto: 'Detectado automaticamente pelo sensor — deixe vazio a menos que seus arquivos estejam fora de <code>/config/www/</code>.',
    ed_folder_hint_manual: 'Apenas a URL base das miniaturas — <b>não</b> lista imagens. Sozinho não mostra nada; defina um sensor acima.',
    ed_columns: 'Colunas',
    ed_image_height: 'Altura da imagem',
    ed_gallery_type: 'Tipo de galeria (botões do lightbox)',
    gt_auto: 'Auto (detectar por imagem)',
    gt_personal: 'Pessoal — Selecionar + Excluir',
    gt_favorites: 'Favoritos — Selecionar + Remover dos favoritos',
    gt_upload: 'Envio — Enviar',
    ed_gallery_type_hint: 'Força quais botões de ação aparecem na prévia em tela cheia para toda a galeria.',
    ed_thumbnails: 'Miniaturas',
    ed_server_thumbs: 'Miniaturas redimensionadas no servidor',
    ed_server_thumbs_hint: 'Recomendado para pastas de originais em tamanho completo — envia miniaturas pequenas ao navegador em vez dos arquivos de vários MB. A imagem completa ainda é usada ao clicar.',
    ed_thumb_width: 'Largura da miniatura (px)',
    ed_actions: 'Ações',
    ed_tv_entity: 'Entidade Frame TV (para «Exibir na TV»)',
    ed_single_tap: 'Toque único',
    ed_double_tap: 'Toque duplo',
    ed_long_press: 'Pressão longa',
    ed_actions_hint: 'As ações aqui seguem o <b>Tipo de galeria</b> acima e precisam da entidade Frame TV.',
  },
  hu: {
    err_define_one: 'Legalább egyet meg kell adnod: folder, sensor, folder_sensor vagy image_list',
    err_tv_required: 'Folder Gallery: a beállított művelethez (koppintás / dupla koppintás / hosszú nyomás) Frame TV entitás szükséges',
    lb_select: 'Kiválasztás',
    lb_unfavourite: '★ Eltávolítás a kedvencekből',
    lb_upload: '⬆ Feltöltés',
    lb_delete: '🗑 Törlés',
    lb_close: 'Bezárás',
    frame_chooser_title: 'Melyik Frame-re töltsük fel?',
    no_images: 'Nem található kép',
    configure_sensor: 'Állíts be egy szenzort vagy image_list-et',
    folder_label: 'Mappa',
    folder_all: 'Mind',
    images_count: '{n} kép',
    removed_fav: 'Eltávolítva a kedvencekből',
    artwork_deleted: 'Kép törölve',
    all_frames: '⬆ Összes Frame ({n})',
    uploading_all: 'Feltöltés az összes Frame-re ({n})…',
    upload_to: 'Feltöltés → {name}',
    cancel: 'Mégse',
    action_executed: 'Művelet végrehajtva: {name}',
    error: 'Hiba: {msg}',
    g_nothing: 'Semmi',
    g_lightbox: 'Előnézet megnyitása (lightbox)',
    g_upload: 'Feltöltés a TV-re',
    g_select: 'Megjelenítés a TV-n',
    g_delete: 'Törlés',
    g_unfavourite: 'Eltávolítás a kedvencekből',
    ed_title: 'Cím',
    ed_sensor: 'Szenzor (a képek listáját adja)',
    ed_sensor_hint: 'Kötelező — egy <code>platform: folder</code> szenzor (pl. <code>sensor.&lt;tv&gt;_store</code>). Ez sorolja fel a képeket.',
    ed_folder: 'Mappa útvonala (opcionális)',
    ed_folder_ph: 'automatikusan a szenzorból',
    ed_folder_hint_auto: 'Automatikusan a szenzorból — hagyd üresen, hacsak a fájljaid nem a <code>/config/www/</code> mappán kívül vannak.',
    ed_folder_hint_manual: 'Csak a bélyegképek alap-URL-je — <b>nem</b> sorolja fel a képeket. Önmagában semmit sem mutat; állíts be fent egy szenzort.',
    ed_columns: 'Oszlopok',
    ed_image_height: 'Képmagasság',
    ed_gallery_type: 'Galéria típusa (lightbox gombok)',
    gt_auto: 'Auto (képenként felismerve)',
    gt_personal: 'Személyes — Kiválasztás + Törlés',
    gt_favorites: 'Kedvencek — Kiválasztás + Eltávolítás a kedvencekből',
    gt_upload: 'Feltöltés — Feltöltés',
    ed_gallery_type_hint: 'Meghatározza, mely műveleti gombok jelenjenek meg a teljes képernyős előnézetben az egész galériára.',
    ed_thumbnails: 'Bélyegképek',
    ed_server_thumbs: 'Szerveroldalon átméretezett bélyegképek',
    ed_server_thumbs_hint: 'Teljes méretű eredetiket tartalmazó mappákhoz ajánlott — kis bélyegképeket küld a böngészőnek a több MB-os fájlok helyett. A teljes kép kattintáskor továbbra is használatban marad.',
    ed_thumb_width: 'Bélyegkép szélessége (px)',
    ed_actions: 'Műveletek',
    ed_tv_entity: 'Frame TV entitás (a „Megjelenítés a TV-n” művelethez)',
    ed_single_tap: 'Egyszeri koppintás',
    ed_double_tap: 'Dupla koppintás',
    ed_long_press: 'Hosszú nyomás',
    ed_actions_hint: 'Az itteni műveletek a fenti <b>Galéria típusát</b> követik, és Frame TV entitást igényelnek.',
  },
};

function fgcTranslate(lang, key, params) {
  const norm = (l) => {
    if (!l) return null;
    if (TRANSLATIONS[l]) return l;
    const base = String(l).split('-')[0];
    return TRANSLATIONS[base] ? base : null;
  };
  const dict = TRANSLATIONS[norm(lang)] || TRANSLATIONS.en;
  let str = dict[key] != null ? dict[key] : (TRANSLATIONS.en[key] != null ? TRANSLATIONS.en[key] : key);
  if (params) {
    Object.keys(params).forEach((p) => {
      str = str.replace(new RegExp('\\{' + p + '\\}', 'g'), params[p]);
    });
  }
  return str;
}

// <fgc-pure-helpers> — pure, no DOM/hass; unit-tested in
// tests/js/folder_gallery_helpers.test.mjs by extracting this region.
function fgcBasename(p) {
  return String(p).match(/[^/]+$/)?.[0] || String(p);
}

// The path of `fullPath` relative to the folder sensor's base dir, always
// starting with "/". A recursive `platform: folder` sensor (filter "**/*")
// lists nested files; keeping the sub-path is what lets the browser fetch
// /local/<base>/<sub>/file.jpg instead of a flattened (and 404-ing) URL.
function fgcRelPath(fullPath, base) {
  const fp = String(fullPath);
  const b = String(base || '').replace(/\/+$/, '');
  if (b && fp.startsWith(b)) {
    const rel = fp.slice(b.length);
    return rel.startsWith('/') ? rel : '/' + rel;
  }
  return '/' + fgcBasename(fp);
}

// The sub-folder of a "/sub/dir/file.jpg" relative path ("" at the root).
function fgcSubfolder(relPath) {
  const parts = String(relPath).replace(/^\//, '').split('/');
  parts.pop(); // drop the filename
  return parts.join('/');
}

// Group normalized images (each with a `subfolder`) into selectable sets:
// an "all" set first, then one per distinct sub-folder, sorted. Returns [] when
// there is nothing to choose between (no sub-folders), so the caller falls back
// to a single implicit set and shows no selector.
function fgcGroupBySubfolder(images, allLabel) {
  const subs = [...new Set(images.map((im) => im.subfolder || ''))];
  const nested = subs.filter((s) => s !== '').sort();
  if (nested.length === 0) return [];
  const sets = [{ key: '__all__', label: allLabel, images }];
  for (const s of nested) {
    sets.push({
      key: s,
      label: s.split('/').pop(),
      images: images.filter((im) => (im.subfolder || '') === s),
    });
  }
  return sets;
}
// </fgc-pure-helpers>

class FolderGalleryCard extends HTMLElement {

  constructor() {
    super();
    this.attachShadow({ mode: 'open' });
    this._images = [];
    this._config = {};
    // Folder selector state (survives gallery re-renders and hass updates).
    this._folderSets = [];
    this._activeFolderKey = null;
    // Double-tap detection state (survives gallery re-renders).
    this._dtIndex = -1;
    this._dtTime = 0;
    this._dtTimer = null;
  }

  static get properties() {
    return {
      hass: {},
      config: {}
    };
  }

  _t(key, params) {
    const lang = (this._hass && (this._hass.locale?.language || this._hass.language)) || 'en';
    return fgcTranslate(lang, key, params);
  }

  setConfig(config) {
    const hasFolders = Array.isArray(config.folders) && config.folders.length > 0;
    if (
      !config.folder &&
      !config.sensor &&
      !config.folder_sensor &&
      !config.image_list &&
      !hasFolders
    ) {
      throw new Error(fgcTranslate('en', 'err_define_one'));
    }

    // A tap / double-tap / long-press action that calls one of our art
    // services must carry a Frame TV entity, otherwise the service call has no
    // target. The visual editor builds these actions, so reject the config
    // here (blocks save) when the entity is missing.
    const needsEntity = (a) => {
      if (!a || typeof a !== 'object') return false;
      const svc = (a.service || a.perform_action || '').toLowerCase();
      if (!svc.startsWith('samsungtv_smart.')) return false;
      const hasEntity =
        (a.target && a.target.entity_id) || (a.data && a.data.entity_id);
      return !hasEntity;
    };
    if (
      needsEntity(config.action) ||
      needsEntity(config.tap_action) ||
      needsEntity(config.double_tap_action) ||
      needsEntity(config.hold_action)
    ) {
      throw new Error(fgcTranslate('en', 'err_tv_required'));
    }

    this._config = {
      title: config.title || '',
      folder: config.folder || null,
      columns: config.columns || 4,
      image_height: config.image_height || '150px',
      aspect_ratio: config.aspect_ratio || null, // e.g., "1" for square, "16/9" for landscape, "3/4" for portrait
      gap: config.gap || '8px',
      border_radius: config.border_radius || '8px',
      show_filename: config.show_filename !== false,
      filter: config.filter || '*',
      action: config.action || null,
      tap_action: config.tap_action || null,
      hold_action: config.hold_action || null,
      double_tap_action: config.double_tap_action || null,
      sensor: config.sensor || null, // Sensor that provides image list
      image_list: config.image_list || null, // Static list of images
      server_thumbnails: config.server_thumbnails !== false, // resize via integration
      thumbnail_width: config.thumbnail_width || 400,
      gallery_type: config.gallery_type || null, // auto | personal | favorites | upload
      ...config
    };
    
    this.render();
  }

  set hass(hass) {
    const oldHass = this._hass;
    this._hass = hass;

    // Anti-flicker: skip re-render if none of the sensors the card consumes
    // changed. Considers `folder_sensor` and `sensor` (visual editor writes the
    // latter) plus every sensor named in a `folders:` entry, and compares all
    // attributes the card reads.
    const sensorEntities = this._configuredSensors();
    if (sensorEntities.length && oldHass) {
      const buildKey = (states) =>
        JSON.stringify(
          sensorEntities.map((e) => {
            const a = states[e]?.attributes;
            return a
              ? [a.file_list, a.images, a.thumbnails, a.items, a.path]
              : null;
          })
        );

      // Skip re-render if no relevant attribute changed (prevents flickering)
      if (buildKey(oldHass.states) === buildKey(hass.states)) {
        return;
      }
    }

    this.updateImages();
  }

  get hass() {
    return this._hass;
  }

  // Every sensor entity the card reads: the top-level sensor plus any named in
  // a `folders:` entry. Used by the anti-flicker check and nothing else.
  _configuredSensors() {
    const out = new Set();
    const add = (s) => {
      if (s.folder_sensor) out.add(s.folder_sensor);
      if (s.sensor) out.add(s.sensor);
    };
    add(this._config);
    if (Array.isArray(this._config.folders)) this._config.folders.forEach(add);
    return [...out];
  }

  // The /local/ base URL for a source's thumbnails: explicit `folder`, else
  // derived from the folder sensor's `path` attribute (/config/www/ → /local/).
  _folderUrlBase(src) {
    let folder = (src.folder || '').replace(/\/+$/, '');
    if (folder.startsWith('/config/www/')) {
      folder = folder.replace(/^\/config\/www\//, '/local/');
    }
    if (!folder) {
      const sensorEntity = src.folder_sensor || src.sensor;
      const sensorPath =
        sensorEntity && this._hass.states[sensorEntity]?.attributes?.path;
      if (typeof sensorPath === 'string') {
        const cleaned = sensorPath.replace(/\/+$/, '');
        if (cleaned.startsWith('/config/www/')) {
          folder = cleaned.replace(/^\/config\/www\//, '/local/');
        }
      }
    }
    return folder;
  }

  // The folder sensor's filesystem base dir, for turning absolute file_list
  // paths into sub-paths relative to the /local/ base.
  _sensorBasePath(src) {
    const sensorEntity = src.folder_sensor || src.sensor;
    const p =
      sensorEntity && this._hass.states[sensorEntity]?.attributes?.path;
    return typeof p === 'string' ? p.replace(/\/+$/, '') : '';
  }

  // Normalize one source ({folder|folder_sensor|sensor|image_list}) into the
  // image objects the gallery renders. Sub-paths are preserved so nested
  // folders resolve (and so group_by_subfolder has something to group on).
  _imagesForSource(src) {
    const folder = this._folderUrlBase(src);
    const base = this._sensorBasePath(src);
    const fromFileList = (fileList) => {
      if (typeof fileList === 'string') {
        fileList = fileList.split(',').map((f) => f.trim()).filter(Boolean);
      }
      if (!Array.isArray(fileList) || fileList.length === 0) return null;
      return fileList.map((f) => {
        const rel = fgcRelPath(f, base); // "/sub/file.jpg" or "/file.jpg"
        const filename = fgcBasename(rel);
        const content_id = filename.replace(/\.[^/.]+$/, '');
        return {
          path: `${folder}${rel}`,
          filename,
          name: content_id,
          content_id,
          subfolder: fgcSubfolder(rel),
        };
      });
    };

    if (src.folder_sensor) {
      const st = this._hass.states[src.folder_sensor];
      const imgs = st && st.attributes ? fromFileList(st.attributes.file_list) : null;
      if (imgs) return imgs;
    }
    if (src.sensor) {
      const st = this._hass.states[src.sensor];
      if (st && st.attributes) {
        if (st.attributes.file_list !== undefined) {
          const imgs = fromFileList(st.attributes.file_list);
          if (imgs) return imgs;
        }
        const generic =
          st.attributes.images ||
          st.attributes.thumbnails ||
          st.attributes.items ||
          [];
        if (generic.length) return this._normalizeGeneric(generic, folder);
      }
    }
    if (src.image_list && src.image_list.length > 0) {
      return this._normalizeGeneric(src.image_list, folder);
    }
    return [];
  }

  _normalizeGeneric(images, folder) {
    return images.map((img) => {
      if (typeof img === 'string') {
        const filename = fgcBasename(img);
        const content_id = filename.replace(/\.[^/.]+$/, '');
        return {
          path: img.startsWith('/local') ? img : `${folder}/${filename}`,
          filename,
          name: content_id,
          content_id,
          subfolder: '',
        };
      }
      return {
        path: img.path || img.url || img.thumbnail || '',
        filename: img.filename || img.name || 'unknown',
        name:
          img.name ||
          img.title ||
          img.filename?.replace(/\.[^/.]+$/, '').replace(/_/g, ' ') ||
          'Unknown',
        content_id: img.content_id || img.id || null,
        subfolder: img.subfolder || '',
        ...img,
      };
    });
  }

  // Build the selectable folder sets. One per `folders:` entry; otherwise, when
  // group_by_subfolder is on, one per sub-folder (plus "All"); otherwise a
  // single implicit set (no selector shown).
  _buildFolderSets() {
    if (Array.isArray(this._config.folders) && this._config.folders.length > 0) {
      return this._config.folders.map((entry, i) => {
        const key = entry.name || entry.sensor || entry.folder_sensor || `folder-${i}`;
        return {
          key,
          label: entry.name || entry.sensor || entry.folder_sensor || `Folder ${i + 1}`,
          images: this._imagesForSource(entry),
        };
      });
    }
    const images = this._imagesForSource(this._config);
    if (this._config.group_by_subfolder) {
      const grouped = fgcGroupBySubfolder(images, this._t('folder_all'));
      if (grouped.length) return grouped;
    }
    return [{ key: '__single__', label: '', images }];
  }

  updateImages() {
    if (!this._hass) return;
    this._folderSets = this._buildFolderSets();
    const active =
      this._folderSets.find((s) => s.key === this._activeFolderKey) ||
      this._folderSets[0];
    this._activeFolderKey = active ? active.key : null;
    this._images = active ? active.images : [];
    this.renderGallery();
  }

  render() {
    this.shadowRoot.innerHTML = `
      <style>
        :host {
          display: block;
        }
        
        ha-card {
          padding: 16px;
          overflow: hidden;
        }
        
        .folder-select {
          display: flex;
          align-items: center;
          gap: 8px;
          padding: 8px 16px 0;
        }
        .folder-select label {
          font-size: 0.9em;
          color: var(--secondary-text-color);
        }
        .folder-select-input {
          flex: 1;
          padding: 6px 8px;
          border-radius: 6px;
          border: 1px solid var(--divider-color, #e0e0e0);
          background: var(--card-background-color, #fff);
          color: var(--primary-text-color);
          font: inherit;
        }

        .card-header {
          font-size: 1.2em;
          font-weight: 500;
          padding-bottom: 12px;
          display: flex;
          justify-content: space-between;
          align-items: center;
        }
        
        .image-count {
          font-size: 0.8em;
          opacity: 0.7;
          font-weight: normal;
        }
        
        .gallery-grid {
          display: grid;
          grid-template-columns: repeat(${this._config.columns}, 1fr);
          gap: ${this._config.gap};
        }
        
        .gallery-item {
          position: relative;
          cursor: pointer;
          border-radius: ${this._config.border_radius};
          overflow: hidden;
          background: var(--card-background-color, #1c1c1c);
          transition: transform 0.2s ease, box-shadow 0.2s ease;
        }
        
        .gallery-item:hover {
          transform: scale(1.03);
          box-shadow: 0 4px 20px rgba(0,0,0,0.3);
          z-index: 1;
        }
        
        .gallery-item:active {
          transform: scale(0.98);
        }
        
        .gallery-item img {
          width: 100%;
          ${this._config.aspect_ratio 
            ? `aspect-ratio: ${this._config.aspect_ratio};` 
            : `height: ${this._config.image_height};`}
          object-fit: cover;
          display: block;
          transition: opacity 0.3s ease;
        }
        
        .gallery-item img.loading {
          opacity: 0.5;
        }
        
        .gallery-item img.error {
          opacity: 0.3;
        }
        
        .image-overlay {
          position: absolute;
          bottom: 0;
          left: 0;
          right: 0;
          background: linear-gradient(transparent, rgba(0,0,0,0.8));
          padding: 8px;
          opacity: 0;
          transition: opacity 0.2s ease;
        }
        
        .gallery-item:hover .image-overlay {
          opacity: 1;
        }
        
        .image-name {
          color: white;
          font-size: 0.75em;
          white-space: nowrap;
          overflow: hidden;
          text-overflow: ellipsis;
        }
        
        .selected {
          outline: 3px solid var(--primary-color, #03a9f4);
          outline-offset: 2px;
        }
        
        .empty-state {
          text-align: center;
          padding: 40px 20px;
          opacity: 0.6;
        }
        
        .empty-state ha-icon {
          --mdc-icon-size: 48px;
          margin-bottom: 12px;
        }
        
        .loading-spinner {
          display: flex;
          justify-content: center;
          padding: 40px;
        }
        
        /* Responsive */
        @media (max-width: 600px) {
          .gallery-grid {
            grid-template-columns: repeat(2, 1fr);
          }
        }
        
        /* Lightbox */
        .lightbox {
          display: none;
          position: fixed;
          top: 0;
          left: 0;
          right: 0;
          bottom: 0;
          background: rgba(0,0,0,0.95);
          z-index: 999;
          justify-content: center;
          align-items: center;
          flex-direction: column;
        }
        
        .lightbox.open {
          display: flex;
        }
        
        .lightbox img {
          max-width: 90vw;
          max-height: 80vh;
          object-fit: contain;
          border-radius: 8px;
        }
        
        .lightbox-close {
          position: absolute;
          top: 20px;
          right: 20px;
          color: white;
          cursor: pointer;
          font-size: 2em;
          opacity: 0.7;
          transition: opacity 0.2s;
        }
        
        .lightbox-close:hover {
          opacity: 1;
        }
        
        .lightbox-actions {
          margin-top: 20px;
          display: flex;
          gap: 12px;
        }
        
        .lightbox-btn {
          background: var(--primary-color, #03a9f4);
          color: white;
          border: none;
          padding: 12px 24px;
          border-radius: 8px;
          cursor: pointer;
          font-size: 1em;
          transition: background 0.2s;
        }
        
        .lightbox-btn:hover {
          background: var(--primary-color-light, #29b6f6);
        }
        
        .lightbox-btn.secondary {
          background: rgba(255,255,255,0.1);
        }
        
        .lightbox-btn.danger {
          background: #e53935;
        }
        
        .lightbox-btn.danger:hover {
          background: #ef5350;
        }

        /* Frame chooser (multi-frame upload routing) */
        .frame-chooser {
          display: none;
          position: fixed;
          top: 0;
          left: 0;
          right: 0;
          bottom: 0;
          background: rgba(0,0,0,0.85);
          z-index: 1000;
          justify-content: center;
          align-items: center;
          flex-direction: column;
          padding: 24px;
        }

        .frame-chooser.open {
          display: flex;
        }

        .frame-chooser-title {
          color: white;
          font-size: 1.1em;
          font-weight: 500;
          margin-bottom: 16px;
          text-align: center;
        }

        .frame-chooser-list {
          display: flex;
          flex-direction: column;
          gap: 10px;
          width: 100%;
          max-width: 360px;
        }

        .frame-chooser-list .lightbox-btn {
          width: 100%;
          text-align: center;
        }
      </style>
      
      <ha-card>
        ${this._config.title ? `
          <div class="card-header">
            <span>${this._config.title}</span>
            <span class="image-count"></span>
          </div>
        ` : ''}
        <div class="gallery-container">
          <div class="loading-spinner">
            <ha-circular-progress indeterminate></ha-circular-progress>
          </div>
        </div>
        
        <div class="lightbox" id="lightbox">
          <span class="lightbox-close" id="lightbox-close">&times;</span>
          <img id="lightbox-img" src="" alt="">
          <div class="lightbox-actions">
            <button class="lightbox-btn" id="lightbox-action" style="display:none">${this._t('lb_select')}</button>
            <button class="lightbox-btn secondary" id="lightbox-unfavourite" style="display:none">${this._t('lb_unfavourite')}</button>
            <button class="lightbox-btn" id="lightbox-upload" style="display:none">${this._t('lb_upload')}</button>
            <button class="lightbox-btn danger" id="lightbox-delete" style="display:none">${this._t('lb_delete')}</button>
            <button class="lightbox-btn secondary" id="lightbox-close-btn">${this._t('lb_close')}</button>
          </div>
        </div>

        <div class="frame-chooser" id="frame-chooser">
          <div class="frame-chooser-title">${this._t('frame_chooser_title')}</div>
          <div class="frame-chooser-list" id="frame-chooser-list"></div>
        </div>
      </ha-card>
    `;

    // Setup lightbox events
    this.setupLightbox();
  }

  _thumbUrl(path) {
    // Route grid thumbnails through the integration's resize endpoint so a
    // folder of full-size originals isn't downloaded at full resolution. Only
    // applies to local files (/local/...); remote/absolute URLs are used as-is.
    // The original path is still used for the lightbox and tap/hold actions.
    if (this._config.server_thumbnails === false) return path;
    if (!path || !path.startsWith('/local/')) return path;
    const w = parseInt(this._config.thumbnail_width, 10) || 400;
    return `/api/samsungtv_smart/thumbnail?path=${encodeURIComponent(path)}&w=${w}`;
  }

  _folderSelectorHtml() {
    // Only shown when there is more than one folder to choose between.
    if (!this._folderSets || this._folderSets.length < 2) return '';
    const options = this._folderSets
      .map(
        (s) =>
          `<option value="${encodeURIComponent(s.key)}"${
            s.key === this._activeFolderKey ? ' selected' : ''
          }>${s.label}</option>`
      )
      .join('');
    return `
      <div class="folder-select">
        <label>${this._t('folder_label')}</label>
        <select class="folder-select-input">${options}</select>
      </div>
    `;
  }

  _wireFolderSelector(container) {
    const select = container.querySelector('.folder-select-input');
    if (!select) return;
    select.addEventListener('change', (e) => {
      this._activeFolderKey = decodeURIComponent(e.target.value);
      const set = this._folderSets.find((s) => s.key === this._activeFolderKey);
      this._images = set ? set.images : [];
      this.renderGallery();
    });
  }

  renderGallery() {
    const container = this.shadowRoot.querySelector('.gallery-container');
    const countEl = this.shadowRoot.querySelector('.image-count');
    
    if (!container) return;
    
    if (countEl) {
      countEl.textContent = this._t('images_count', { n: this._images.length });
    }

    const selector = this._folderSelectorHtml();

    if (this._images.length === 0) {
      container.innerHTML = `
        ${selector}
        <div class="empty-state">
          <ha-icon icon="mdi:image-off"></ha-icon>
          <div>${this._t('no_images')}</div>
          <div style="font-size: 0.8em; margin-top: 8px;">
            ${this._t('configure_sensor')}
          </div>
        </div>
      `;
      this._wireFolderSelector(container);
      return;
    }

    container.innerHTML = `
      ${selector}
      <div class="gallery-grid">
        ${this._images.map((img, index) => `
          <div class="gallery-item" data-index="${index}" data-path="${img.path}" data-content-id="${img.content_id || ''}">
            <img src="${this._thumbUrl(img.path)}" alt="${img.name}" loading="lazy" decoding="async" class="loading"
                 onerror="this.classList.add('error')"
                 onload="this.classList.remove('loading')">
            ${this._config.show_filename ? `
              <div class="image-overlay">
                <div class="image-name">${img.name}</div>
              </div>
            ` : ''}
          </div>
        `).join('')}
      </div>
    `;

    this._wireFolderSelector(container);

    // Add click + long-press handlers.
    // Long-press is detected with a pointer timer rather than the `contextmenu`
    // event: contextmenu doesn't fire reliably on touch / the HA Companion app,
    // and a `click` always follows the release — which used to open the lightbox
    // (tap_action) on top of, or instead of, the hold_action. Here a fired
    // long-press swallows that trailing click.
    const LONG_PRESS_MS = 500;
    const MOVE_TOLERANCE = 10;
    const DOUBLE_TAP_MS = 300;
    const hasDoubleTap = !!this._config.double_tap_action;
    container.querySelectorAll('.gallery-item').forEach(item => {
      let pressTimer = null;
      let holdFired = false;
      let startX = 0;
      let startY = 0;

      const clearTimer = () => {
        if (pressTimer) {
          clearTimeout(pressTimer);
          pressTimer = null;
        }
      };

      item.addEventListener('pointerdown', (e) => {
        holdFired = false;
        startX = e.clientX;
        startY = e.clientY;
        clearTimer();
        pressTimer = setTimeout(() => {
          holdFired = true;
          this.handleHold(e, item);
        }, LONG_PRESS_MS);
      });

      item.addEventListener('pointermove', (e) => {
        // Treat a finger/cursor drag as a scroll, not a press.
        if (Math.abs(e.clientX - startX) > MOVE_TOLERANCE ||
            Math.abs(e.clientY - startY) > MOVE_TOLERANCE) {
          clearTimer();
        }
      });

      ['pointerup', 'pointerleave', 'pointercancel'].forEach((evt) =>
        item.addEventListener(evt, clearTimer));

      item.addEventListener('click', (e) => {
        if (holdFired) {
          // A long-press already handled this interaction; swallow the click so
          // tap_action (e.g. lightbox) doesn't also fire on release.
          e.preventDefault();
          e.stopPropagation();
          holdFired = false;
          return;
        }

        // Without a double_tap_action, fire the single tap immediately (no
        // added latency). With one configured, debounce: a second click within
        // the window is a double tap; otherwise the single tap fires on timeout.
        if (!hasDoubleTap) {
          this.handleClick(e, item);
          return;
        }

        // Double-tap state lives on the instance (keyed by image index), not in
        // this closure: a sensor update can re-render the gallery between the
        // two clicks, recreating the element and losing any per-element timer —
        // which made the second click look like a fresh first click and opened
        // the lightbox instead of firing double_tap_action.
        const index = parseInt(item.dataset.index);
        const now = Date.now();
        if (this._dtIndex === index && (now - this._dtTime) < DOUBLE_TAP_MS) {
          if (this._dtTimer) {
            clearTimeout(this._dtTimer);
            this._dtTimer = null;
          }
          this._dtIndex = -1;
          this._dtTime = 0;
          this.executeAction(this._images[index], this._config.double_tap_action);
        } else {
          this._dtIndex = index;
          this._dtTime = now;
          if (this._dtTimer) clearTimeout(this._dtTimer);
          this._dtTimer = setTimeout(() => {
            this._dtTimer = null;
            this._dtIndex = -1;
            this.handleClick(e, item);
          }, DOUBLE_TAP_MS);
        }
      });

      // Suppress the native context menu on long-press / right-click so it
      // doesn't interrupt the hold.
      item.addEventListener('contextmenu', (e) => e.preventDefault());
    });
  }

  setupLightbox() {
    const lightbox = this.shadowRoot.getElementById('lightbox');
    const closeBtn = this.shadowRoot.getElementById('lightbox-close');
    const closeBtnAlt = this.shadowRoot.getElementById('lightbox-close-btn');
    const actionBtn = this.shadowRoot.getElementById('lightbox-action');
    const unfavouriteBtn = this.shadowRoot.getElementById('lightbox-unfavourite');
    const uploadBtn = this.shadowRoot.getElementById('lightbox-upload');
    const deleteBtn = this.shadowRoot.getElementById('lightbox-delete');

    if (closeBtn) closeBtn.addEventListener('click', () => this.closeLightbox());
    if (closeBtnAlt) closeBtnAlt.addEventListener('click', () => this.closeLightbox());
    if (lightbox) {
      lightbox.addEventListener('click', (e) => {
        if (e.target === lightbox) this.closeLightbox();
      });
    }
    if (actionBtn) {
      actionBtn.addEventListener('click', () => {
        if (this._selectedImage) {
          this.executeAction(this._selectedImage);
          this.closeLightbox();
        }
      });
    }
    if (unfavouriteBtn) {
      unfavouriteBtn.addEventListener('click', () => {
        if (this._selectedImage) {
          this._callUnfavourite(this._selectedImage);
          this.closeLightbox();
        }
      });
    }
    if (uploadBtn) {
      uploadBtn.addEventListener('click', () => {
        if (this._selectedImage) {
          this.executeAction(this._selectedImage, this._config.action);
          this.closeLightbox();
        }
      });
    }
    if (deleteBtn) {
      deleteBtn.addEventListener('click', () => {
        if (this._selectedImage) {
          this._callDelete(this._selectedImage);
          this.closeLightbox();
        }
      });
    }
  }

  openLightbox(imageData) {
    const lightbox = this.shadowRoot.getElementById('lightbox');
    const img = this.shadowRoot.getElementById('lightbox-img');
    const actionBtn = this.shadowRoot.getElementById('lightbox-action');
    const unfavouriteBtn = this.shadowRoot.getElementById('lightbox-unfavourite');
    const uploadBtn = this.shadowRoot.getElementById('lightbox-upload');
    const deleteBtn = this.shadowRoot.getElementById('lightbox-delete');

    if (!lightbox || !img) return;

    this._selectedImage = imageData;
    img.src = imageData.path;
    lightbox.classList.add('open');

    // Which action buttons to show. When `gallery_type` is set, it forces the
    // button set for the whole gallery, so e.g. an upload folder always offers
    // Upload even if a filename happens to look like a content id. Otherwise
    // fall back to per-image detection from the content_id prefix.
    let showSelect;
    let showUnfavourite;
    let showUpload;
    let showDelete;

    const gt = (this._config.gallery_type || 'auto').toLowerCase();
    if (gt === 'personal') {
      showSelect = true;  showUnfavourite = false; showUpload = false; showDelete = true;
    } else if (gt === 'favorites' || gt === 'favourites') {
      showSelect = true;  showUnfavourite = true;  showUpload = false; showDelete = false;
    } else if (gt === 'upload') {
      showSelect = false; showUnfavourite = false; showUpload = true;  showDelete = false;
    } else {
      // auto: infer from the content id
      const contentId = imageData.content_id || imageData.name || '';
      const upper = contentId.toUpperCase();
      const isSam = upper.startsWith('SAM-') || upper.startsWith('SAM_');
      const isMy = upper.startsWith('MY-') || upper.startsWith('MY_');
      const isOther = !isSam && !isMy;
      showSelect = isSam || isMy;
      showUnfavourite = isSam;
      showUpload = isOther;
      showDelete = isMy;
    }

    if (actionBtn) actionBtn.style.display = showSelect ? 'inline-block' : 'none';
    if (unfavouriteBtn) unfavouriteBtn.style.display = showUnfavourite ? 'inline-block' : 'none';
    if (uploadBtn) uploadBtn.style.display = showUpload ? 'inline-block' : 'none';
    if (deleteBtn) deleteBtn.style.display = showDelete ? 'inline-block' : 'none';
  }

  _getEntityId() {
    if (this._config.action && this._config.action.target && this._config.action.target.entity_id)
      return this._config.action.target.entity_id;
    if (this._config.action && this._config.action.data && this._config.action.data.entity_id)
      return this._config.action.data.entity_id;
    // Fallback to the dedicated Frame TV entity, so the lightbox buttons still
    // resolve a target even when no `action` is configured (e.g. tap opens the
    // lightbox and there is no separate tap action).
    return this._config.frame_tv_entity || this._config.tv_entity || '';
  }

  _callUnfavourite(imageData) {
    if (!this._hass) return;
    const entityId = this._getEntityId();
    const contentId = imageData.content_id || imageData.name || '';
    this._hass.callService(
      'samsungtv_smart', 'art_set_favourite',
      { content_id: contentId, status: 'off' },
      { entity_id: entityId }
    );
    this.showToast(this._t('removed_fav'));
  }

  _callDelete(imageData) {
    if (!this._hass) return;
    const entityId = this._getEntityId();
    const contentId = imageData.content_id || imageData.name || '';
    this._hass.callService(
      'samsungtv_smart', 'art_delete',
      { content_id: contentId },
      { entity_id: entityId }
    );
    this.showToast(this._t('artwork_deleted'));
  }

  closeLightbox() {
    const lightbox = this.shadowRoot.getElementById('lightbox');
    if (lightbox) {
      lightbox.classList.remove('open');
      this._selectedImage = null;
    }
  }

  handleClick(e, item) {
    const index = parseInt(item.dataset.index);
    const imageData = this._images[index];

    // Modern object-form tap_action, e.g.
    //   tap_action:
    //     action: perform-action      # (or the legacy call-service)
    //     perform_action: samsungtv_smart.art_select_image
    //     target: {...}
    //     data: {...}
    // HA's own cards accept this shape; handle it here so the new syntax works
    // instead of silently doing nothing (the dispatcher used to only read the
    // legacy `action: { service: ... }` block).
    const tapAction = this._config.tap_action;
    if (tapAction && typeof tapAction === 'object') {
      if (tapAction.action === 'more-info' && this._config.entity) {
        this.fireEvent('hass-more-info', { entityId: this._config.entity });
      } else if (tapAction.action === 'none') {
        // explicitly do nothing
      } else {
        this.executeAction(imageData, tapAction);
      }
      return;
    }

    // If tap_action is 'lightbox' or not defined with action, show lightbox
    if (this._config.tap_action === 'lightbox' ||
        (!this._config.tap_action && this._config.action)) {
      this.openLightbox(imageData);
    }
    // Direct action on tap
    else if (this._config.tap_action === 'action' || this._config.action) {
      this.executeAction(imageData);
    }
    // More info
    else if (this._config.tap_action === 'more-info' && this._config.entity) {
      this.fireEvent('hass-more-info', { entityId: this._config.entity });
    }
  }

  handleHold(e, item) {
    e.preventDefault();
    const index = parseInt(item.dataset.index);
    const imageData = this._images[index];
    
    if (this._config.hold_action) {
      this.executeAction(imageData, this._config.hold_action);
    } else {
      this.openLightbox(imageData);
    }
  }

  executeAction(imageData, actionConfig = null) {
    const action = actionConfig || this._config.action;
    if (!action || !this._hass) return;
    // Accept both the legacy `service:` key and the modern `perform_action:`
    // key (HA renamed call-service -> perform-action).
    if (!action.service && !action.perform_action) return;

    // Multi-frame upload routing.
    // When the action is an upload and the gallery points at a local photo
    // source (folder not store/personal AND image not a SAM-/MY_ content id),
    // route to a Frame that is actually there rather than to whatever entity
    // the YAML happens to name. Every other case keeps the original behaviour.
    if (this._isUploadAction(action) && this._isLocalPhotoSource(imageData)) {
      const frames = this._discoverFrames();
      if (frames.length > 1) {
        this._openFrameChooser(imageData, action, frames);
        return;
      }
      if (frames.length === 1) {
        // Exactly one Frame: use it. Discovery only lists TVs currently
        // exposing art_mode_status, so a configured-but-unreachable TV drops
        // out of the list -- and silently falling back to the configured
        // entity sent uploads to a TV that was powered off or removed, with no
        // chooser shown to reveal the mismatch.
        this._dispatchAction(imageData, action, frames[0].entity_id);
        return;
      }
    }

    this._dispatchAction(imageData, action);
  }

  _dispatchAction(imageData, action, entityOverride = null) {
    const service = action ? (action.service || action.perform_action) : null;
    if (!action || !this._hass || !service) return Promise.resolve();

    const [domain, serviceName] = service.split('.');

    // Build service data with template substitution
    let serviceData = { ...(action.data || {}) };

    // Replace templates
    const replaceTemplates = (obj) => {
      if (typeof obj === 'string') {
        return obj
          .replace(/\{\{image_path\}\}/g, imageData.path)
          .replace(/\{\{file_path\}\}/g, imageData.path.replace('/local/', '/config/www/'))
          .replace(/\{\{filename\}\}/g, imageData.filename)
          .replace(/\{\{name\}\}/g, imageData.name)
          .replace(/\{\{content_id\}\}/g, imageData.content_id || '')
          .replace(/\{\{index\}\}/g, imageData.index || '');
      }
      if (typeof obj === 'object' && obj !== null) {
        const result = Array.isArray(obj) ? [] : {};
        for (const key in obj) {
          result[key] = replaceTemplates(obj[key]);
        }
        return result;
      }
      return obj;
    };

    serviceData = replaceTemplates(serviceData);

    // Add target if specified
    let target = action.target ? replaceTemplates(action.target) : undefined;

    // Route to the chosen Frame when an override is supplied. Preserve the
    // configured placement: override target.entity_id if a target was used,
    // otherwise set entity_id in the service data.
    if (entityOverride) {
      if (target && target.entity_id !== undefined) {
        target = { ...target, entity_id: entityOverride };
      } else {
        serviceData = { ...serviceData, entity_id: entityOverride };
      }
    }

    console.log(`[FolderGalleryCard] Calling ${service}`, { serviceData, target });

    return this._hass.callService(domain, serviceName, serviceData, target)
      .then(() => {
        // Visual feedback
        this.showToast(this._t('action_executed', { name: serviceName }));
      })
      .catch(err => {
        console.error('[FolderGalleryCard] Service call failed:', err);
        this.showToast(this._t('error', { msg: err.message }), true);
      });
  }

  _isUploadAction(action) {
    const svc = (action.service || action.perform_action || '').toLowerCase();
    if (svc.endsWith('art_upload')) return true;
    // Generic heuristic: an upload-style action carries a file_path payload.
    return !!(action.data && Object.prototype.hasOwnProperty.call(action.data, 'file_path'));
  }

  _isLocalPhotoSource(imageData) {
    // Guard 1: the configured folder must not be a TV-resident art folder
    // (last path segment "store" or "personal").
    const folder = (this._config.folder || '').replace(/\/+$/, '').toLowerCase();
    const lastSeg = folder.split('/').pop() || '';
    if (lastSeg === 'store' || lastSeg === 'personal') return false;

    // Guard 2: the image must not already be a TV content id (SAM-/MY_).
    const cid = String(imageData.content_id || imageData.name || '').toUpperCase();
    const isTvContent =
      cid.startsWith('SAM-') || cid.startsWith('SAM_') ||
      cid.startsWith('MY-') || cid.startsWith('MY_');
    return !isTvContent;
  }

  _discoverFrames() {
    // A Frame TV exposes the `art_mode_status` attribute on its media_player
    // (added by extra_state_attributes only when Art Mode is supported).
    const frames = [];
    const states = (this._hass && this._hass.states) || {};
    for (const entityId in states) {
      if (!entityId.startsWith('media_player.')) continue;
      const st = states[entityId];
      if (st && st.attributes &&
          Object.prototype.hasOwnProperty.call(st.attributes, 'art_mode_status')) {
        frames.push({
          entity_id: entityId,
          name: st.attributes.friendly_name || entityId
        });
      }
    }
    frames.sort((a, b) => a.name.localeCompare(b.name));
    return frames;
  }

  _openFrameChooser(imageData, action, frames) {
    const chooser = this.shadowRoot.getElementById('frame-chooser');
    const list = this.shadowRoot.getElementById('frame-chooser-list');
    if (!chooser || !list) {
      // Fallback: chooser markup missing, dispatch to the first frame.
      this._dispatchAction(imageData, action, frames[0] ? frames[0].entity_id : null);
      return;
    }

    list.innerHTML = '';

    // "All Frames" option
    const allBtn = document.createElement('button');
    allBtn.className = 'lightbox-btn';
    allBtn.textContent = this._t('all_frames', { n: frames.length });
    allBtn.addEventListener('click', async () => {
      this._closeFrameChooser();
      this.showToast(this._t('uploading_all', { n: frames.length }));
      for (const f of frames) {
        // Sequential: each upload is a heavy WebSocket transfer.
        await this._dispatchAction(imageData, action, f.entity_id);
      }
    });
    list.appendChild(allBtn);

    // One option per frame
    frames.forEach(f => {
      const btn = document.createElement('button');
      btn.className = 'lightbox-btn';
      btn.textContent = f.name;
      btn.addEventListener('click', () => {
        this._closeFrameChooser();
        this.showToast(this._t('upload_to', { name: f.name }));
        this._dispatchAction(imageData, action, f.entity_id);
      });
      list.appendChild(btn);
    });

    // Cancel
    const cancelBtn = document.createElement('button');
    cancelBtn.className = 'lightbox-btn secondary';
    cancelBtn.textContent = this._t('cancel');
    cancelBtn.addEventListener('click', () => this._closeFrameChooser());
    list.appendChild(cancelBtn);

    // Dismiss on backdrop click
    chooser.onclick = (e) => {
      if (e.target === chooser) this._closeFrameChooser();
    };

    chooser.classList.add('open');
  }

  _closeFrameChooser() {
    const chooser = this.shadowRoot.getElementById('frame-chooser');
    if (chooser) chooser.classList.remove('open');
  }

  showToast(message, isError = false) {
    this.fireEvent('hass-notification', {
      message: message,
      duration: 3000
    });
  }

  fireEvent(type, detail = {}) {
    const event = new CustomEvent(type, {
      bubbles: true,
      composed: true,
      detail
    });
    this.dispatchEvent(event);
  }

  getCardSize() {
    const rows = Math.ceil(this._images.length / this._config.columns);
    return Math.max(1, rows * 2);
  }

  static getConfigElement() {
    return document.createElement('folder-gallery-card-editor');
  }

  static getStubConfig() {
    return {
      title: 'My Gallery',
      folder: '/local/images',
      columns: 4,
      image_height: '150px',
      sensor: 'sensor.folder_images'
    };
  }
}

// Card Editor
class FolderGalleryCardEditor extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: 'open' });
  }

  _lang() {
    return (this._hass && (this._hass.locale?.language || this._hass.language)) || 'en';
  }

  _t(key, params) {
    return fgcTranslate(this._lang(), key, params);
  }

  set hass(hass) {
    const oldLang = this._lang();
    this._hass = hass;
    // Re-render once the UI language is known / changes, since setConfig may
    // have rendered before hass (and therefore the language) was available.
    if (this._config && this._lang() !== oldLang) {
      this.render();
    }
  }

  get hass() {
    return this._hass;
  }

  setConfig(config) {
    this._config = config;
    this.render();
  }

  // --- helpers to map between config actions and the simple dropdowns ------

  _actionKind(action) {
    // Map a config action back to a gesture preset:
    // select | upload | delete | unfavourite | none.
    if (!action || typeof action !== 'object') return 'none';
    const svc = (action.service || action.perform_action || '').toLowerCase();
    if (svc.includes('art_select_image')) return 'select';
    if (svc.includes('art_upload')) return 'upload';
    if (svc.includes('art_delete')) return 'delete';
    if (svc.includes('art_set_favourite')) return 'unfavourite';
    return 'none';
  }

  _tapKind() {
    const t = this._config.tap_action;
    if (t === 'lightbox') return 'lightbox';
    if (t === 'action') return this._actionKind(this._config.action);
    if (typeof t === 'object') return this._actionKind(t);
    return 'none';
  }

  _tvEntity() {
    const from = (a) =>
      a && typeof a === 'object' && a.target && a.target.entity_id
        ? a.target.entity_id
        : null;
    return (
      this._config.frame_tv_entity ||
      this._config.tv_entity ||
      from(this._config.action) ||
      from(this._config.double_tap_action) ||
      from(this._config.hold_action) ||
      ''
    );
  }

  // Gesture presets available for the current gallery_type. Each entry is
  // { v: kind, l: label }. `lightbox` (preview) is offered for single tap only.
  _gestureOptions(includeLightbox) {
    const gt = (this._config.gallery_type || 'auto').toLowerCase();
    const opts = [{ v: 'none', l: this._t('g_nothing') }];
    if (includeLightbox) opts.push({ v: 'lightbox', l: this._t('g_lightbox') });
    if (gt === 'upload') {
      opts.push({ v: 'upload', l: this._t('g_upload') });
    } else if (gt === 'personal') {
      opts.push({ v: 'select', l: this._t('g_select') });
      opts.push({ v: 'delete', l: this._t('g_delete') });
    } else if (gt === 'favorites' || gt === 'favourites') {
      opts.push({ v: 'select', l: this._t('g_select') });
      opts.push({ v: 'unfavourite', l: this._t('g_unfavourite') });
    } else {
      opts.push({ v: 'select', l: this._t('g_select') });
    }
    return opts;
  }

  // Build the config action object for a gesture preset (all need the TV entity).
  // When the TV entity is empty we still build the action but omit `target`, so
  // the card's setConfig can detect the missing entity and refuse to save.
  _buildAction(kind, tvEntity) {
    if (kind === 'none' || !kind) return null;
    const target = tvEntity ? { entity_id: tvEntity } : undefined;
    switch (kind) {
      case 'select':
        return {
          perform_action: 'samsungtv_smart.art_select_image',
          target,
          data: { content_id: '{{content_id}}' },
        };
      case 'upload':
        return {
          perform_action: 'samsungtv_smart.art_upload',
          target,
          data: { file_path: '{{file_path}}' },
        };
      case 'delete':
        return {
          perform_action: 'samsungtv_smart.art_delete',
          target,
          data: { content_id: '{{content_id}}' },
        };
      case 'unfavourite':
        return {
          perform_action: 'samsungtv_smart.art_set_favourite',
          target,
          data: { content_id: '{{content_id}}', status: 'off' },
        };
      default:
        return null;
    }
  }

  render() {
    const tapKind = this._tapKind();
    const dtapKind = this._actionKind(this._config.double_tap_action);
    const holdKind = this._actionKind(this._config.hold_action);
    const tvEntity = this._tvEntity();
    const sel = (v, cur) => (v === cur ? 'selected' : '');

    this.shadowRoot.innerHTML = `
      <style>
        .form-row {
          margin-bottom: 12px;
        }
        .form-row label {
          display: block;
          margin-bottom: 4px;
          font-weight: 500;
        }
        .form-row input, .form-row select {
          width: 100%;
          padding: 8px;
          border: 1px solid var(--divider-color);
          border-radius: 4px;
          background: var(--card-background-color);
          color: var(--primary-text-color);
        }
        .form-row input[type="checkbox"] { width: auto; margin-right: 8px; }
        .form-row .hint {
          display: block;
          margin-top: 4px;
          font-size: 0.8em;
          font-weight: normal;
          color: var(--secondary-text-color);
        }
        .section { margin: 16px 0 4px; font-weight: 700; opacity: 0.8; }
      </style>

      <div class="form-row">
        <label>${this._t('ed_title')}</label>
        <input type="text" id="title" value="${this._config.title || ''}">
      </div>

      <div class="form-row">
        <label>${this._t('ed_sensor')}</label>
        <input type="text" id="folder_sensor" value="${this._config.folder_sensor || this._config.sensor || ''}" placeholder="sensor.your_folder_sensor">
        <span class="hint">${this._t('ed_sensor_hint')}</span>
      </div>

      <div class="form-row">
        <label>${this._t('ed_folder')}</label>
        <input type="text" id="folder" value="${this._config.folder || ''}" placeholder="${this._t('ed_folder_ph')}">
        <span class="hint">${
          (this._config.folder_sensor || this._config.sensor)
            ? this._t('ed_folder_hint_auto')
            : this._t('ed_folder_hint_manual')
        }</span>
      </div>

      <div class="form-row">
        <label>${this._t('ed_columns')}</label>
        <input type="number" id="columns" value="${this._config.columns || 4}" min="1" max="10">
      </div>

      <div class="form-row">
        <label>${this._t('ed_image_height')}</label>
        <input type="text" id="image_height" value="${this._config.image_height || '150px'}">
      </div>

      <div class="form-row">
        <label>${this._t('ed_gallery_type')}</label>
        <select id="gallery_type">
          <option value="auto" ${sel('auto', (this._config.gallery_type || 'auto'))}>${this._t('gt_auto')}</option>
          <option value="personal" ${sel('personal', this._config.gallery_type)}>${this._t('gt_personal')}</option>
          <option value="favorites" ${sel('favorites', this._config.gallery_type)}>${this._t('gt_favorites')}</option>
          <option value="upload" ${sel('upload', this._config.gallery_type)}>${this._t('gt_upload')}</option>
        </select>
        <span class="hint">${this._t('ed_gallery_type_hint')}</span>
      </div>

      <div class="section">${this._t('ed_thumbnails')}</div>
      <div class="form-row">
        <label><input type="checkbox" id="server_thumbnails" ${this._config.server_thumbnails !== false ? 'checked' : ''}>${this._t('ed_server_thumbs')}</label>
        <span class="hint">${this._t('ed_server_thumbs_hint')}</span>
      </div>
      <div class="form-row">
        <label>${this._t('ed_thumb_width')}</label>
        <input type="number" id="thumbnail_width" value="${this._config.thumbnail_width || 400}" min="64" max="1024">
      </div>

      <div class="section">${this._t('ed_actions')}</div>
      <div class="form-row">
        <label>${this._t('ed_tv_entity')}</label>
        <input type="text" id="_tv_entity" value="${tvEntity}" placeholder="media_player.samsung_frame">
      </div>
      <div class="form-row">
        <label>${this._t('ed_single_tap')}</label>
        <select id="_tap_kind">
          ${this._gestureOptions(true)
            .map((o) => `<option value="${o.v}" ${sel(o.v, tapKind)}>${o.l}</option>`)
            .join('')}
        </select>
      </div>
      <div class="form-row">
        <label>${this._t('ed_double_tap')}</label>
        <select id="_dtap_kind">
          ${this._gestureOptions(false)
            .map((o) => `<option value="${o.v}" ${sel(o.v, dtapKind)}>${o.l}</option>`)
            .join('')}
        </select>
      </div>
      <div class="form-row">
        <label>${this._t('ed_long_press')}</label>
        <select id="_hold_kind">
          ${this._gestureOptions(false)
            .map((o) => `<option value="${o.v}" ${sel(o.v, holdKind)}>${o.l}</option>`)
            .join('')}
        </select>
        <span class="hint">${this._t('ed_actions_hint')}</span>
      </div>
    `;

    const ids = [
      'title',
      'folder',
      'folder_sensor',
      'columns',
      'image_height',
      'gallery_type',
      'server_thumbnails',
      'thumbnail_width',
      '_tv_entity',
      '_tap_kind',
      '_dtap_kind',
      '_hold_kind',
    ];
    ids.forEach((id) => {
      const el = this.shadowRoot.getElementById(id);
      if (el) {
        el.addEventListener('change', () => this._emit());
      }
    });
  }

  _emit() {
    const g = (id) => this.shadowRoot.getElementById(id);
    const cfg = { ...this._config };

    cfg.title = g('title').value || '';
    const sensor = g('folder_sensor').value.trim();
    cfg.folder_sensor = sensor || undefined;
    const folder = g('folder').value.trim();
    cfg.folder = folder || undefined;
    cfg.columns = parseInt(g('columns').value, 10) || 4;
    cfg.image_height = g('image_height').value || '150px';
    cfg.server_thumbnails = g('server_thumbnails').checked;
    cfg.thumbnail_width = parseInt(g('thumbnail_width').value, 10) || 400;
    const gt = g('gallery_type').value;
    cfg.gallery_type = gt && gt !== 'auto' ? gt : undefined;

    const tv = g('_tv_entity').value.trim();
    // Persist the Frame TV entity on its own key so the lightbox buttons can
    // always resolve a target, even when there is no `action` configured.
    cfg.frame_tv_entity = tv || undefined;

    // Only keep gesture kinds that are valid for the selected gallery type
    // (e.g. switching to "upload" drops a stale "select"/"delete" choice).
    const validTap = new Set(this._gestureOptions(true).map((o) => o.v));
    const validGesture = new Set(this._gestureOptions(false).map((o) => o.v));
    const clamp = (kind, valid) => (valid.has(kind) ? kind : 'none');

    // Single tap
    const tapKind = clamp(g('_tap_kind').value, validTap);
    if (tapKind === 'lightbox') {
      cfg.tap_action = 'lightbox';
      // The fullscreen-preview buttons (Display on TV / Unfavourite / Delete /
      // Upload) need a Frame TV entity + service to dispatch: the Select/Upload
      // buttons run `config.action` and Unfavourite/Delete read the entity from
      // it. Build the gallery type's primary action instead of clearing it,
      // otherwise the lightbox opens but every button silently does nothing.
      const gt = (cfg.gallery_type || 'auto').toLowerCase();
      const primary = gt === 'upload' ? 'upload' : 'select';
      const act = this._buildAction(primary, tv);
      cfg.action = act || undefined;
    } else if (tapKind !== 'none') {
      const act = this._buildAction(tapKind, tv);
      cfg.tap_action = act ? 'action' : undefined;
      cfg.action = act || undefined;
    } else {
      cfg.tap_action = undefined;
      cfg.action = undefined;
    }

    // Double tap / long press
    cfg.double_tap_action =
      this._buildAction(clamp(g('_dtap_kind').value, validGesture), tv) || undefined;
    cfg.hold_action =
      this._buildAction(clamp(g('_hold_kind').value, validGesture), tv) || undefined;

    Object.keys(cfg).forEach((k) => cfg[k] === undefined && delete cfg[k]);
    this._config = cfg;
    this.fireEvent('config-changed', { config: cfg });
  }

  fireEvent(type, detail) {
    const event = new CustomEvent(type, {
      bubbles: true,
      composed: true,
      detail
    });
    this.dispatchEvent(event);
  }
}

// Register elements
customElements.define('folder-gallery-card', FolderGalleryCard);
customElements.define('folder-gallery-card-editor', FolderGalleryCardEditor);

// Register with Lovelace
window.customCards = window.customCards || [];
window.customCards.push({
  type: 'folder-gallery-card',
  name: 'Folder Gallery Card',
  description: 'Display images from a folder with click actions',
  preview: true
});

console.info(
  '%c FOLDER-GALLERY-CARD %c v1.6.0 ',
  'color: white; background: #03a9f4; font-weight: bold;',
  'color: #03a9f4; background: white; font-weight: bold;'
);
